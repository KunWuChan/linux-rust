# SRCU 代码深度分析

## 目录
1. [核心函数分析](#核心函数分析)
2. [内存屏障详解](#内存屏障详解)
3. [宽限期状态机](#宽限期状态机)
4. [组合树机制](#组合树机制)
5. [性能优化技巧](#性能优化技巧)

---

## 核心函数分析

### 1. __srcu_read_lock() - 读取端加锁

#### 代码实现

```c
int __srcu_read_lock(struct srcu_struct *ssp)
{
    struct srcu_ctr __percpu *scp = READ_ONCE(ssp->srcu_ctrp);
    
    this_cpu_inc(scp->srcu_locks.counter);
    smp_mb(); /* B */  /* Avoid leaking the critical section. */
    return __srcu_ptr_to_ctr(ssp, scp);
}
```

#### 关键点分析

1. **读取 srcu_ctrp**：
   - 使用 `READ_ONCE()` 确保原子读取
   - `srcu_ctrp` 指向当前宽限期使用的计数器对（idx 0 或 1）

2. **增加锁定计数**：
   - `this_cpu_inc()` 增加当前 CPU 的 `srcu_locks` 计数器
   - 这是每 CPU 操作，性能高

3. **内存屏障 B**：
   - 确保计数器增加在临界区代码之前完成
   - 防止编译器重排序

4. **返回索引**：
   - 将指针转换为索引（0 或 1）
   - 必须传递给对应的 `__srcu_read_unlock()`

#### 内存顺序保证

```
读取者视角：
  [读取 srcu_ctrp] -> [增加 locks] -> [smp_mb() B] -> [临界区代码]
  
更新者视角：
  [检查 locks == unlocks] -> [smp_mb() E] -> [切换 srcu_ctrp]
```

### 2. __srcu_read_unlock() - 读取端解锁

#### 代码实现

```c
void __srcu_read_unlock(struct srcu_struct *ssp, int idx)
{
    smp_mb(); /* C */  /* Avoid leaking the critical section. */
    this_cpu_inc(__srcu_ctr_to_ptr(ssp, idx)->srcu_unlocks.counter);
}
```

#### 关键点分析

1. **内存屏障 C**：
   - 确保临界区内的所有操作在计数器增加之前完成
   - 防止临界区代码"泄漏"到解锁之后

2. **增加解锁计数**：
   - 使用索引找到对应的计数器
   - 注意：可能在不同的 CPU 上执行（如果任务迁移）

3. **为什么需要索引？**：
   - 因为 `srcu_ctrp` 可能在读取期间被切换
   - 使用索引确保操作正确的计数器对

#### 内存顺序保证

```
读取者视角：
  [临界区代码] -> [smp_mb() C] -> [增加 unlocks]
  
更新者视角：
  [切换 srcu_ctrp] -> [smp_mb() D] -> [检查 locks == unlocks]
```

### 3. srcu_flip() - 切换计数器索引

#### 代码实现

```c
static void srcu_flip(struct srcu_struct *ssp)
{
    smp_mb(); /* E */  /* Pairs with B and C. */
    
    WRITE_ONCE(ssp->srcu_ctrp,
               &ssp->sda->srcu_ctrs[!(ssp->srcu_ctrp - &ssp->sda->srcu_ctrs[0])]);
    
    smp_mb(); /* D */  /* Pairs with C. */
}
```

#### 关键点分析

1. **内存屏障 E**：
   - 与读取端的屏障 B 和 C 配对
   - 确保在切换前，所有之前的检查都完成

2. **切换逻辑**：
   ```c
   // 当前是 idx 0，切换到 idx 1
   // 当前是 idx 1，切换到 idx 0
   new_idx = !old_idx;
   ```

3. **内存屏障 D**：
   - 与读取端的屏障 C 配对
   - 确保切换后，新的读取者看到新的计数器

#### 切换时机

- 在 SCAN1 阶段：从 idx 0 切换到 idx 1（或相反）
- 在 SCAN2 阶段：再次切换，确保所有 CPU 看到切换

### 4. process_srcu() - 宽限期处理核心

#### 状态转换流程

```c
static void process_srcu(struct work_struct *work)
{
    struct srcu_usage *sup = container_of(work, ...);
    struct srcu_struct *ssp = sup->srcu_ssp;
    int idx;
    
    // 检查是否需要启动宽限期
    if (rcu_seq_state(sup->srcu_gp_seq) == SRCU_STATE_IDLE) {
        if (ULONG_CMP_GE(sup->srcu_gp_seq, sup->srcu_gp_seq_needed))
            return; // 没有待处理的宽限期
        
        // 启动宽限期：IDLE -> SCAN1
        srcu_gp_start(ssp);
    }
    
    // SCAN1 阶段：等待旧索引的读取者退出
    idx = rcu_seq_ctr(sup->srcu_gp_seq) & 0x1;
    if (rcu_seq_state(sup->srcu_gp_seq) == SRCU_STATE_SCAN1) {
        if (srcu_readers_active_idx_check(ssp, idx)) {
            // 所有读取者已退出，切换到 SCAN2
            srcu_flip(ssp);
            rcu_seq_set_state(&sup->srcu_gp_seq, SRCU_STATE_SCAN2);
        } else {
            // 还有读取者，稍后重试
            srcu_reschedule(ssp, SRCU_INTERVAL);
            return;
        }
    }
    
    // SCAN2 阶段：再次等待，确保所有 CPU 看到切换
    idx = !idx;
    if (rcu_seq_state(sup->srcu_gp_seq) == SRCU_STATE_SCAN2) {
        if (srcu_readers_active_idx_check(ssp, idx)) {
            // 所有读取者已退出，宽限期完成
            rcu_seq_set_state(&sup->srcu_gp_seq, SRCU_STATE_IDLE);
            srcu_invoke_callbacks(ssp);
        } else {
            // 还有读取者，稍后重试
            srcu_reschedule(ssp, SRCU_INTERVAL);
            return;
        }
    }
    
    // 检查是否有新的宽限期请求
    if (ULONG_CMP_LT(sup->srcu_gp_seq, sup->srcu_gp_seq_needed)) {
        srcu_reschedule(ssp, 0); // 立即处理下一个
    }
}
```

#### 关键步骤详解

1. **SCAN1 阶段**：
   - 目标：等待所有使用旧索引的读取者退出
   - 方法：检查 `srcu_locks[idx] == srcu_unlocks[idx]`
   - 切换：当检查通过时，调用 `srcu_flip()` 切换索引

2. **SCAN2 阶段**：
   - 目标：确保所有 CPU 看到索引切换
   - 方法：再次检查新索引的读取者
   - 完成：当检查通过时，宽限期完成

3. **为什么需要两个阶段？**：
   - 防止竞争条件
   - 确保所有 CPU 的内存一致性
   - 处理 CPU 迁移的情况

### 5. srcu_readers_active_idx_check() - 检查读取者

#### 代码实现

```c
static bool srcu_readers_active_idx_check(struct srcu_struct *ssp, int idx)
{
    bool did_gp;
    unsigned long rdm;
    unsigned long unlocks;
    
    unlocks = srcu_readers_unlock_idx(ssp, idx, &rdm);
    did_gp = !!(rdm & SRCU_READ_FLAVOR_SLOWGP);
    
    // 内存屏障：确保在检查 locks 之前，unlocks 的读取完成
    smp_mb(); /* A */
    
    // 检查 locks 是否等于 unlocks
    return srcu_readers_lock_idx(ssp, idx, did_gp, unlocks);
}
```

#### 关键点分析

1. **读取 unlocks**：
   - 遍历所有 CPU，累加 `srcu_unlocks[idx]`
   - 同时检查读取者类型（flavor）

2. **内存屏障 A**：
   - 确保 unlocks 的读取在 locks 的读取之前完成
   - 防止编译器重排序

3. **检查 locks**：
   - 遍历所有 CPU，累加 `srcu_locks[idx]`
   - 比较 `locks == unlocks`

4. **为什么需要检查两者？**：
   - 确保所有读取者都已退出
   - 处理计数器溢出的情况

---

## 内存屏障详解

### 内存屏障配对关系

```
读取者路径：
  __srcu_read_lock():
    [读取 srcu_ctrp] -> [增加 locks] -> [smp_mb() B]
    
  __srcu_read_unlock():
    [smp_mb() C] -> [增加 unlocks]
    
更新者路径：
  srcu_readers_active_idx_check():
    [读取 unlocks] -> [smp_mb() A] -> [读取 locks]
    
  srcu_flip():
    [smp_mb() E] -> [切换 srcu_ctrp] -> [smp_mb() D]
```

### 屏障配对说明

1. **B 和 E 配对**：
   - B：确保 locks 增加在临界区之前
   - E：确保切换在检查完成之后
   - 保证：如果更新者看到 locks 增加，读取者使用的是旧索引

2. **C 和 D 配对**：
   - C：确保临界区在 unlocks 增加之前完成
   - D：确保切换在 unlocks 检查之前完成
   - 保证：如果更新者看到 unlocks 增加，读取者已完成临界区

3. **A 的作用**：
   - 确保 unlocks 的读取在 locks 的读取之前
   - 防止检查顺序错误

### 内存顺序保证

```
时间线示例：

CPU 0 (读取者):
  T1: srcu_read_lock() -> 增加 locks
  T2: [临界区代码]
  T3: srcu_read_unlock() -> 增加 unlocks

CPU 1 (更新者):
  T4: 检查 unlocks (看到 T3 的结果)
  T5: 内存屏障 A
  T6: 检查 locks (看到 T1 的结果)
  T7: 如果 locks == unlocks，切换索引

保证：
  - 如果 T6 看到 locks 增加，那么 T1 在 T6 之前
  - 如果 T4 看到 unlocks 增加，那么 T3 在 T4 之前
  - 如果 locks == unlocks，那么所有读取者都已完成
```

---

## 宽限期状态机

### 状态定义

```c
#define SRCU_STATE_IDLE    0  // 空闲状态
#define SRCU_STATE_SCAN1   1  // 第一次扫描
#define SRCU_STATE_SCAN2   2  // 第二次扫描
```

### 状态转换图

```
        [启动宽限期]
             |
             v
    ┌────────────────┐
    │     IDLE       │
    │  (无宽限期)     │
    └────────────────┘
             |
             | srcu_gp_start()
             v
    ┌────────────────┐
    │    SCAN1       │
    │ (等待旧索引)    │
    └────────────────┘
             |
             | 所有读取者退出
             | srcu_flip()
             v
    ┌────────────────┐
    │    SCAN2       │
    │ (确保切换可见)  │
    └────────────────┘
             |
             | 所有读取者退出
             | 执行回调
             v
    ┌────────────────┐
    │     IDLE       │
    │  (宽限期完成)   │
    └────────────────┘
```

### 序列号编码

```c
// 序列号格式：[状态(2位)][计数器(62位)]
// 状态在低 2 位
#define rcu_seq_state(s) ((s) & 0x3)

// 计数器在高 62 位
#define rcu_seq_ctr(s) ((s) >> 2)
```

### 序列号操作

```c
// 启动序列号
static void rcu_seq_start(unsigned long *sp)
{
    *sp += 1; // IDLE(0) -> SCAN1(1)
}

// 设置状态
static void rcu_seq_set_state(unsigned long *sp, int state)
{
    *sp = (*sp & ~0x3) | state;
}

// 检查是否完成
static bool rcu_seq_done(unsigned long *sp, unsigned long s)
{
    return ULONG_CMP_GE(*sp, s);
}
```

---

## 组合树机制

### 树结构

```
                    [根节点]
                   /        \
              [节点1]      [节点2]
             /      \      /      \
        [CPU0-1] [CPU2-3] [CPU4-5] [CPU6-7]
```

### srcu_node 结构

```c
struct srcu_node {
    spinlock_t lock;
    unsigned long srcu_have_cbs[4];        // 子节点的回调序列号
    unsigned long srcu_data_have_cbs[4];   // CPU 位掩码
    struct srcu_node *srcu_parent;        // 父节点指针
    int grplo, grphi;                     // 管理的 CPU 范围
};
```

### 回调传播机制

```c
static void srcu_funnel_gp_start(struct srcu_struct *ssp, 
                                 struct srcu_data *sdp,
                                 unsigned long s, bool do_norm)
{
    struct srcu_node *snp = sdp->mynode;
    
    // 从叶子节点向上传播
    for (; snp != NULL; snp = snp->srcu_parent) {
        spin_lock_irqsave_rcu_node(snp, flags);
        
        // 更新节点的回调信息
        if (ULONG_CMP_LT(snp->srcu_have_cbs[idx], s)) {
            snp->srcu_have_cbs[idx] = s;
            snp->srcu_data_have_cbs[idx] |= sdp->grpmask;
        }
        
        spin_unlock_irqrestore_rcu_node(snp, flags);
    }
    
    // 到达根节点，启动宽限期
    if (rcu_seq_state(sup->srcu_gp_seq) == SRCU_STATE_IDLE) {
        srcu_gp_start(ssp);
    }
}
```

### 优势

1. **减少锁竞争**：
   - 每个节点有自己的锁
   - 减少全局锁的竞争

2. **提高可扩展性**：
   - 树结构适应多 CPU 系统
   - 回调信息分层管理

3. **优化宽限期启动**：
   - 只在根节点启动宽限期
   - 减少不必要的检查

---

## 性能优化技巧

### 1. 使用 fast 版本

```c
// 普通版本：需要 smp_mb()
int idx = srcu_read_lock(ssp);

// Fast 版本：不需要 smp_mb()（但需要 RCU watching）
struct srcu_ctr __percpu *scp = srcu_read_lock_fast(ssp);
```

**适用场景**：
- 读取端在 RCU watching 上下文中
- 性能敏感路径
- 不需要 NMI 安全

### 2. 使用异步回调

```c
// 同步等待（阻塞）
synchronize_srcu(ssp);

// 异步回调（非阻塞）
call_srcu(ssp, &rhp, callback_func);
```

**适用场景**：
- 更新操作不紧急
- 可以异步处理清理工作
- 避免阻塞关键路径

### 3. 批量处理更新

```c
// 低效：多次宽限期
for (i = 0; i < n; i++) {
    update_data(i);
    synchronize_srcu(ssp);
}

// 高效：一次宽限期
for (i = 0; i < n; i++) {
    update_data(i);
}
synchronize_srcu(ssp);
```

### 4. 使用 expedited 版本（谨慎）

```c
// 普通版本：可能较慢
synchronize_srcu(ssp);

// Expedited 版本：更快但影响性能
synchronize_srcu_expedited(ssp);
```

**注意事项**：
- 会增加系统负载
- 可能影响其他宽限期
- 只在必要时使用

### 5. 减少读取端临界区长度

```c
// 不好：临界区太长
int idx = srcu_read_lock(ssp);
// ... 大量计算 ...
// ... 可能睡眠的操作 ...
srcu_read_unlock(ssp, idx);

// 好：临界区尽可能短
int idx = srcu_read_lock(ssp);
data = srcu_dereference(ptr, ssp);
srcu_read_unlock(ssp, idx);
// ... 在临界区外处理数据 ...
```

---

## 总结

### 关键设计原则

1. **两阶段宽限期**：确保所有 CPU 看到索引切换
2. **内存屏障配对**：保证正确的内存顺序
3. **组合树结构**：提高多 CPU 系统的可扩展性
4. **每 CPU 计数器**：减少锁竞争，提高性能

### 性能考虑

1. **读取端**：尽可能短，避免长时间持有
2. **更新端**：批量处理，使用异步回调
3. **宽限期**：避免不必要的 expedited 调用
4. **数据结构**：根据系统大小选择合适的实现

### 调试建议

1. **使用 lockdep**：检测死锁和错误使用
2. **添加检查点**：在关键位置验证状态
3. **跟踪宽限期**：监控序列号和状态
4. **分析性能**：测量延迟和竞争情况

---

*最后更新: 2024*

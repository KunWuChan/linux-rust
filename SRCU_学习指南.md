# SRCU (Sleepable Read-Copy-Update) 完整学习指南

## 目录
1. [概述](#概述)
2. [架构设计](#架构设计)
3. [执行流程](#执行流程)
4. [调试方法](#调试方法)
5. [代码示例](#代码示例)
6. [常见问题](#常见问题)

---

## 概述

### 什么是 SRCU？

SRCU (Sleepable Read-Copy-Update) 是 Linux 内核中的一种同步机制，允许在读取端临界区中睡眠。与传统的 RCU 不同，SRCU 的读取端可以调用可能睡眠的函数。

### 核心特性

- **可睡眠的读取端**：读取端临界区可以调用可能睡眠的函数
- **两阶段宽限期**：使用两个索引（idx 0 和 1）来跟踪读取者
- **多种实现**：支持 Tiny SRCU 和 Tree SRCU 两种实现
- **多种读取者类型**：支持普通、NMI-safe 和 fast 三种读取者类型

### 与 RCU 的区别

| 特性 | RCU | SRCU |
|------|-----|------|
| 读取端睡眠 | ❌ 不允许 | ✅ 允许 |
| 性能 | 更高 | 相对较低 |
| 使用场景 | 高性能路径 | 需要睡眠的场景 |

---

## 架构设计

### 1. 数据结构层次

#### Tree SRCU 架构（多 CPU 系统）

```
srcu_struct (顶层结构)
├── srcu_usage (更新端数据)
│   ├── srcu_node[] (组合树节点)
│   │   ├── srcu_have_cbs[4] (回调序列号)
│   │   └── srcu_data_have_cbs[4] (CPU 位掩码)
│   └── srcu_gp_seq (宽限期序列号)
└── srcu_data[] (每 CPU 数据)
    ├── srcu_ctrs[2] (读取者计数器)
    │   ├── srcu_locks (锁定计数)
    │   └── srcu_unlocks (解锁计数)
    └── srcu_cblist (回调列表)
```

#### Tiny SRCU 架构（单 CPU 系统）

```
srcu_struct (简化结构)
├── srcu_lock_nesting[2] (嵌套深度)
├── srcu_idx (当前索引)
├── srcu_cb_head/tail (回调链表)
└── srcu_wq (等待队列)
```

### 2. 核心数据结构

#### srcu_struct (Tree 版本)

```c
struct srcu_struct {
    struct srcu_ctr __percpu *srcu_ctrp;  // 每 CPU 计数器指针
    struct srcu_data __percpu *sda;       // 每 CPU 数据数组
    struct lockdep_map dep_map;            // Lockdep 映射
    struct srcu_usage *srcu_sup;          // 更新端数据
};
```

#### srcu_data (每 CPU 数据)

```c
struct srcu_data {
    // 读取端状态
    struct srcu_ctr srcu_ctrs[2];          // 两个索引的计数器
    int srcu_reader_flavor;                // 读取者类型
    
    // 更新端状态
    spinlock_t lock;                       // 保护锁
    struct rcu_segcblist srcu_cblist;     // 回调列表
    unsigned long srcu_gp_seq_needed;      // 需要的 GP 序列号
    struct srcu_node *mynode;              // 关联的树节点
    int cpu;                               // CPU 编号
};
```

#### srcu_node (组合树节点)

```c
struct srcu_node {
    spinlock_t lock;
    unsigned long srcu_have_cbs[4];        // 子节点回调序列号
    unsigned long srcu_data_have_cbs[4];  // CPU 位掩码
    struct srcu_node *srcu_parent;        // 父节点
    int grplo, grphi;                      // CPU 范围
};
```

### 3. 两阶段宽限期机制

SRCU 使用两个索引（idx 0 和 1）来跟踪读取者：

```
时间线：
  GP 0 (idx=0)     GP 1 (idx=1)     GP 2 (idx=0)     GP 3 (idx=1)
  |                |                |                |
  v                v                v                v
[读取者使用 idx=0] [读取者使用 idx=1] [读取者使用 idx=0] [读取者使用 idx=1]
```

**工作原理**：
1. 当前宽限期使用索引 `idx`
2. 新读取者使用索引 `(idx + 1) & 1`
3. 当所有使用旧索引的读取者退出后，宽限期完成

---

## 执行流程

### 1. 初始化流程

```c
// 初始化 SRCU 结构
int init_srcu_struct(struct srcu_struct *ssp)
{
    // 1. 分配 srcu_usage 结构
    // 2. 初始化每 CPU 数据 (srcu_data)
    // 3. 可选：初始化组合树 (srcu_node)
    // 4. 设置初始序列号
}
```

**关键步骤**：
1. 分配 `srcu_usage` 结构
2. 初始化每 CPU `srcu_data` 数组
3. 根据系统大小决定是否使用组合树
4. 初始化宽限期序列号为 `SRCU_GP_SEQ_INITIAL_VAL`

### 2. 读取端流程

#### srcu_read_lock()

```c
int srcu_read_lock(struct srcu_struct *ssp)
{
    // 1. 获取当前索引
    int idx = READ_ONCE(ssp->srcu_sup->srcu_gp_seq) & 0x1;
    
    // 2. 增加对应索引的锁定计数
    this_cpu_inc(ssp->sda->srcu_ctrs[idx].srcu_locks);
    
    // 3. 内存屏障确保顺序
    smp_mb();
    
    return idx;
}
```

**执行步骤**：
1. 读取当前宽限期序列号，确定使用哪个索引（0 或 1）
2. 增加对应 CPU 和索引的 `srcu_locks` 计数器
3. 执行内存屏障，确保后续读取看到正确的数据
4. 返回索引，供 `srcu_read_unlock()` 使用

#### srcu_read_unlock()

```c
void srcu_read_unlock(struct srcu_struct *ssp, int idx)
{
    // 1. 内存屏障
    smp_mb();
    
    // 2. 增加对应索引的解锁计数
    this_cpu_inc(ssp->sda->srcu_ctrs[idx].srcu_unlocks);
}
```

**执行步骤**：
1. 执行内存屏障，确保临界区内的操作完成
2. 增加对应 CPU 和索引的 `srcu_unlocks` 计数器

### 3. 更新端流程

#### synchronize_srcu()

```c
void synchronize_srcu(struct srcu_struct *ssp)
{
    // 1. 注册回调
    call_srcu(ssp, &rs.head, wakeme_after_rcu);
    
    // 2. 等待回调执行（宽限期完成）
    wait_for_completion(&rs.completion);
}
```

**执行步骤**：
1. 调用 `call_srcu()` 注册回调
2. 触发宽限期处理（如果尚未开始）
3. 等待宽限期完成（所有旧读取者退出）
4. 回调被执行，唤醒等待者

#### process_srcu() - 宽限期处理核心

```c
static void process_srcu(struct work_struct *work)
{
    struct srcu_usage *sup = container_of(work, ...);
    
    // 阶段 1: 扫描阶段 (SCAN1)
    // - 切换索引
    // - 等待旧索引的读取者退出
    
    // 阶段 2: 扫描阶段 (SCAN2)  
    // - 再次切换索引
    // - 确保所有读取者看到新索引
    
    // 阶段 3: 空闲阶段 (IDLE)
    // - 执行回调
    // - 准备下一个宽限期
}
```

**宽限期状态机**：

```
IDLE -> SCAN1 -> SCAN2 -> IDLE
  |       |        |        |
  |       |        |        +-- 执行回调
  |       |        +----------- 等待读取者
  |       +-------------------- 切换索引
  +---------------------------- 初始状态
```

**详细流程**：

1. **SCAN1 阶段**：
   - 将 `srcu_gp_seq` 从 `IDLE` 切换到 `SCAN1`
   - 新读取者开始使用新索引
   - 等待所有旧索引的读取者退出

2. **SCAN2 阶段**：
   - 将 `srcu_gp_seq` 从 `SCAN1` 切换到 `SCAN2`
   - 确保所有 CPU 看到索引切换
   - 再次等待读取者退出

3. **IDLE 阶段**：
   - 将 `srcu_gp_seq` 从 `SCAN2` 切换到 `IDLE`
   - 执行所有准备好的回调
   - 检查是否有新的宽限期请求

### 4. 回调处理流程

#### call_srcu()

```c
void call_srcu(struct srcu_struct *ssp, struct rcu_head *rhp,
               rcu_callback_t func)
{
    // 1. 设置回调函数
    rhp->func = func;
    
    // 2. 添加到每 CPU 回调列表
    rcu_segcblist_enqueue(&sdp->srcu_cblist, rhp);
    
    // 3. 触发宽限期（如果需要）
    srcu_gp_start_if_needed(ssp);
}
```

**执行步骤**：
1. 将回调添加到当前 CPU 的 `srcu_cblist`
2. 更新 `srcu_gp_seq_needed` 序列号
3. 如果宽限期未运行，启动宽限期处理

#### srcu_invoke_callbacks()

```c
static void srcu_invoke_callbacks(struct work_struct *work)
{
    struct srcu_data *sdp = container_of(work, ...);
    
    // 1. 从回调列表中取出就绪的回调
    // 2. 执行每个回调函数
    // 3. 处理延迟回调
}
```

---

## 调试方法

### 1. 内核配置选项

```bash
# 启用 SRCU 调试
CONFIG_PROVE_RCU=y          # 启用 RCU 验证
CONFIG_DEBUG_LOCK_ALLOC=y   # 启用锁分配调试
CONFIG_RCU_TRACE=y          # 启用 RCU 跟踪
```

### 2. 运行时调试

#### 检查 SRCU 状态

```c
// 在代码中添加检查点
if (srcu_read_lock_held(ssp)) {
    pr_info("在 SRCU 读取端临界区中\n");
}
```

#### 使用 lockdep

```bash
# 启用 lockdep 检查
echo 1 > /proc/sys/kernel/lockdep

# 查看 lockdep 报告
dmesg | grep -i srcu
```

### 3. 统计信息

#### Tree SRCU 统计

```c
// 打印 SRCU 统计信息
void srcu_torture_stats_print(struct srcu_struct *ssp, 
                              char *tt, char *tf)
{
    // 打印每个 CPU 的锁定/解锁计数
    // 打印宽限期序列号
    // 打印回调统计
}
```

#### 查看 /proc 接口

```bash
# 查看 RCU 统计（如果支持）
cat /proc/rcu/srcu_*
```

### 4. 常见调试技巧

#### 检查死锁

```c
// 使用 lockdep 检查
srcu_lock_acquire(&ssp->dep_map);
// ... 临界区代码 ...
srcu_lock_release(&ssp->dep_map);
```

#### 跟踪宽限期

```c
// 获取当前宽限期状态
unsigned long cookie = get_state_synchronize_srcu(ssp);

// 检查宽限期是否完成
bool done = poll_state_synchronize_srcu(ssp, cookie);
```

#### 使用 printk 调试

```c
// 在关键点添加调试输出
pr_info("SRCU: GP seq=%lu, idx=%d, locks=%lu, unlocks=%lu\n",
        ssp->srcu_sup->srcu_gp_seq,
        idx,
        atomic_long_read(&sdp->srcu_ctrs[idx].srcu_locks),
        atomic_long_read(&sdp->srcu_ctrs[idx].srcu_unlocks));
```

### 5. 使用 ftrace 跟踪

```bash
# 启用 SRCU 跟踪点
echo 1 > /sys/kernel/debug/tracing/events/rcu/enable

# 查看跟踪输出
cat /sys/kernel/debug/tracing/trace
```

### 6. 使用 KASAN 检测内存错误

```bash
# 编译时启用 KASAN
CONFIG_KASAN=y

# 运行时检测内存访问错误
```

### 7. 使用 rcutorture 测试

```bash
# 加载 rcutorture 模块
modprobe rcutorture

# 查看测试结果
dmesg | grep -i torture
```

---

## 代码示例

### 示例 1: 基本使用

```c
#include <linux/srcu.h>

// 定义 SRCU 结构
static DEFINE_SRCU(my_srcu);

// 读取端
void reader_function(void)
{
    int idx;
    
    // 进入读取端临界区
    idx = srcu_read_lock(&my_srcu);
    
    // 可以在这里睡眠
    // 访问受保护的数据
    // ...
    
    // 退出读取端临界区
    srcu_read_unlock(&my_srcu, idx);
}

// 更新端
void updater_function(void)
{
    // 等待所有读取者退出
    synchronize_srcu(&my_srcu);
    
    // 现在可以安全地更新数据
    // ...
}
```

### 示例 2: 使用回调

```c
static void my_callback(struct rcu_head *head)
{
    struct my_data *data = container_of(head, struct my_data, rcu);
    
    // 清理数据
    kfree(data);
}

void update_with_callback(void)
{
    struct my_data *old_data;
    
    // 获取旧数据指针
    old_data = srcu_dereference(ptr, &my_srcu);
    
    // 更新指针
    rcu_assign_pointer(ptr, new_data);
    
    // 异步清理旧数据
    call_srcu(&my_srcu, &old_data->rcu, my_callback);
}
```

### 示例 3: 动态初始化

```c
static struct srcu_struct *my_srcu;

int init_module(void)
{
    // 分配 SRCU 结构
    my_srcu = kmalloc(sizeof(*my_srcu), GFP_KERNEL);
    if (!my_srcu)
        return -ENOMEM;
    
    // 初始化
    if (init_srcu_struct(my_srcu)) {
        kfree(my_srcu);
        return -ENOMEM;
    }
    
    return 0;
}

void cleanup_module(void)
{
    // 清理
    cleanup_srcu_struct(my_srcu);
    kfree(my_srcu);
}
```

### 示例 4: 嵌套使用

```c
void nested_reader(void)
{
    int idx1, idx2;
    
    // 第一层嵌套
    idx1 = srcu_read_lock(&my_srcu);
    
    // 可以嵌套
    idx2 = srcu_read_lock(&my_srcu);
    
    // ... 临界区代码 ...
    
    // 按相反顺序解锁
    srcu_read_unlock(&my_srcu, idx2);
    srcu_read_unlock(&my_srcu, idx1);
}
```

---

## 常见问题

### Q1: 什么时候使用 SRCU 而不是 RCU？

**A**: 当读取端需要调用可能睡眠的函数时，使用 SRCU。例如：
- 需要获取互斥锁
- 需要等待信号量
- 需要分配内存（可能睡眠）

### Q2: SRCU 的性能如何？

**A**: SRCU 的性能低于 RCU，因为：
- 需要维护每 CPU 计数器
- 宽限期处理更复杂
- 需要等待所有读取者退出

### Q3: 可以在中断处理程序中使用 SRCU 吗？

**A**: 
- 普通 `srcu_read_lock()` 不能在中断中使用
- 可以使用 `srcu_read_lock_nmisafe()` 在 NMI 安全场景中使用
- 更新端不能在中断中使用

### Q4: 如何调试 SRCU 死锁？

**A**: 
1. 启用 `CONFIG_PROVE_RCU`
2. 使用 lockdep 检查
3. 检查是否有在 SRCU 读取端调用 `synchronize_srcu()` 的情况
4. 使用 ftrace 跟踪调用链

### Q5: SRCU 的宽限期有多长？

**A**: 宽限期长度取决于：
- 最长的读取端临界区持续时间
- 系统负载
- CPU 数量

通常比 RCU 宽限期长，因为读取端可以睡眠。

### Q6: 如何选择 Tiny SRCU 还是 Tree SRCU？

**A**: 
- **Tiny SRCU**: 单 CPU 或小型系统，代码更简单
- **Tree SRCU**: 多 CPU 系统，性能更好，支持组合树

内核会根据配置自动选择。

### Q7: srcu_read_lock() 返回的索引是什么？

**A**: 索引（0 或 1）用于标识当前宽限期使用的计数器对。必须将相同的索引传递给对应的 `srcu_read_unlock()`。

### Q8: 可以在 SRCU 读取端调用 synchronize_srcu() 吗？

**A**: **不可以！** 这会导致死锁，因为 `synchronize_srcu()` 会等待所有读取者（包括自己）退出。

---

## 参考资料

1. **内核源码**:
   - `include/linux/srcu.h` - SRCU 接口定义
   - `kernel/rcu/srcutree.c` - Tree SRCU 实现
   - `kernel/rcu/srcutiny.c` - Tiny SRCU 实现

2. **内核文档**:
   - `Documentation/RCU/` - RCU 相关文档

3. **相关论文**:
   - "Sleepable Read-Copy Update" by Paul McKenney

4. **调试工具**:
   - `tools/testing/selftests/rcutorture/` - 测试工具
   - `kernel/rcu/rcutorture.c` - 压力测试

---

## 总结

SRCU 是 Linux 内核中重要的同步机制，允许在读取端睡眠，适用于需要复杂同步操作的场景。理解其架构、执行流程和调试方法对于内核开发至关重要。

**关键要点**：
1. SRCU 使用两阶段宽限期机制（两个索引）
2. 读取端可以睡眠，但性能低于 RCU
3. 更新端必须等待所有读取者退出
4. 不能在读取端调用 `synchronize_srcu()`
5. 使用适当的调试工具可以快速定位问题

---

*最后更新: 2024*

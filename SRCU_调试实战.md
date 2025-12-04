# SRCU 调试实战指南

## 目录
1. [调试工具和技巧](#调试工具和技巧)
2. [常见问题诊断](#常见问题诊断)
3. [性能分析](#性能分析)
4. [实战案例](#实战案例)

---

## 调试工具和技巧

### 1. 使用 printk 进行调试

#### 添加调试宏

```c
// 在代码中添加条件编译的调试输出
#ifdef DEBUG_SRCU
#define srcu_dbg(fmt, ...) \
    printk(KERN_DEBUG "SRCU[%s:%d]: " fmt, \
           __func__, __LINE__, ##__VA_ARGS__)
#else
#define srcu_dbg(fmt, ...) do { } while (0)
#endif

// 使用示例
void my_srcu_function(struct srcu_struct *ssp)
{
    srcu_dbg("进入函数，ssp=%p\n", ssp);
    int idx = srcu_read_lock(ssp);
    srcu_dbg("获取锁，idx=%d\n", idx);
    // ...
    srcu_read_unlock(ssp, idx);
    srcu_dbg("释放锁\n");
}
```

#### 跟踪宽限期状态

```c
static void trace_srcu_gp_state(struct srcu_struct *ssp)
{
    struct srcu_usage *sup = ssp->srcu_sup;
    unsigned long gp_seq = READ_ONCE(sup->srcu_gp_seq);
    int state = rcu_seq_state(gp_seq);
    
    pr_info("SRCU GP State: seq=%lu, state=%d (%s)\n",
            gp_seq, state,
            state == SRCU_STATE_IDLE ? "IDLE" :
            state == SRCU_STATE_SCAN1 ? "SCAN1" : "SCAN2");
}
```

#### 跟踪读取者计数

```c
static void trace_srcu_readers(struct srcu_struct *ssp)
{
    int cpu;
    int idx;
    
    for (idx = 0; idx < 2; idx++) {
        unsigned long total_locks = 0;
        unsigned long total_unlocks = 0;
        
        for_each_possible_cpu(cpu) {
            struct srcu_data *sdp = per_cpu_ptr(ssp->sda, cpu);
            total_locks += atomic_long_read(&sdp->srcu_ctrs[idx].srcu_locks);
            total_unlocks += atomic_long_read(&sdp->srcu_ctrs[idx].srcu_unlocks);
        }
        
        pr_info("SRCU idx=%d: locks=%lu, unlocks=%lu, active=%ld\n",
                idx, total_locks, total_unlocks, total_locks - total_unlocks);
    }
}
```

### 2. 使用 ftrace 跟踪

#### 启用 SRCU 跟踪点

```bash
# 查看可用的跟踪点
ls /sys/kernel/debug/tracing/events/rcu/

# 启用所有 RCU 相关跟踪点
echo 1 > /sys/kernel/debug/tracing/events/rcu/enable

# 或者只启用特定跟踪点
echo 1 > /sys/kernel/debug/tracing/events/rcu/rcu_utilization/enable
```

#### 跟踪函数调用

```bash
# 跟踪 srcu_read_lock
echo 'srcu_read_lock' > /sys/kernel/debug/tracing/set_ftrace_filter
echo function > /sys/kernel/debug/tracing/current_tracer
echo 1 > /sys/kernel/debug/tracing/tracing_on

# 执行测试
# ...

# 查看跟踪结果
cat /sys/kernel/debug/tracing/trace
```

#### 使用 trace-cmd

```bash
# 记录跟踪数据
trace-cmd record -e rcu -e srcu

# 查看跟踪数据
trace-cmd report
```

### 3. 使用 Kprobes 动态插桩

```c
#include <linux/kprobes.h>

// 定义 kprobe
static struct kprobe kp = {
    .symbol_name = "srcu_read_lock",
};

// 前置处理函数
static int handler_pre(struct kprobe *p, struct pt_regs *regs)
{
    struct srcu_struct *ssp = (struct srcu_struct *)regs->di;
    pr_info("srcu_read_lock called: ssp=%p\n", ssp);
    return 0;
}

// 后置处理函数
static void handler_post(struct kprobe *p, struct pt_regs *regs,
                         unsigned long flags)
{
    int idx = (int)regs->ax;
    pr_info("srcu_read_lock returned: idx=%d\n", idx);
}

// 注册 kprobe
static int init_kprobes(void)
{
    kp.pre_handler = handler_pre;
    kp.post_handler = handler_post;
    
    if (register_kprobe(&kp) < 0) {
        pr_err("register_kprobe failed\n");
        return -1;
    }
    return 0;
}
```

### 4. 使用 SystemTap 跟踪

```stap
# SRCU 跟踪脚本
probe kernel.function("srcu_read_lock")
{
    printf("srcu_read_lock: ssp=%p\n", $ssp);
}

probe kernel.function("srcu_read_unlock")
{
    printf("srcu_read_unlock: ssp=%p, idx=%d\n", $ssp, $idx);
}

probe kernel.function("synchronize_srcu")
{
    printf("synchronize_srcu: ssp=%p\n", $ssp);
}
```

### 5. 使用 perf 分析

```bash
# 记录 SRCU 相关事件
perf record -e 'rcu:*' -a sleep 10

# 分析性能
perf report

# 查看调用图
perf report --call-graph
```

---

## 常见问题诊断

### 问题 1: 死锁检测

#### 症状
- 系统挂起
- `synchronize_srcu()` 永远不返回
- 内核日志中出现 lockdep 警告

#### 诊断步骤

```c
// 1. 检查是否在读取端调用 synchronize_srcu()
static void check_deadlock(struct srcu_struct *ssp)
{
    if (srcu_read_lock_held(ssp)) {
        pr_warn("警告：在 SRCU 读取端调用 synchronize_srcu() 会导致死锁！\n");
        dump_stack();
    }
}

// 2. 使用 lockdep 检查
void my_function(struct srcu_struct *ssp)
{
    // lockdep 会自动检测死锁
    synchronize_srcu(ssp);  // 如果在这里，lockdep 会警告
}
```

#### 解决方案

```c
// 错误示例
void bad_function(struct srcu_struct *ssp)
{
    int idx = srcu_read_lock(ssp);
    // ...
    synchronize_srcu(ssp);  // ❌ 死锁！
    // ...
    srcu_read_unlock(ssp, idx);
}

// 正确示例
void good_function(struct srcu_struct *ssp)
{
    int idx = srcu_read_lock(ssp);
    // ...
    srcu_read_unlock(ssp, idx);
    
    // 在读取端外调用
    synchronize_srcu(ssp);  // ✅ 正确
}
```

### 问题 2: 宽限期不完成

#### 症状
- `synchronize_srcu()` 长时间阻塞
- 读取者计数不为零

#### 诊断代码

```c
static void diagnose_gp_stuck(struct srcu_struct *ssp)
{
    struct srcu_usage *sup = ssp->srcu_sup;
    int cpu;
    int idx;
    unsigned long gp_seq = READ_ONCE(sup->srcu_gp_seq);
    
    idx = rcu_seq_state(gp_seq) == SRCU_STATE_SCAN1 ? 0 : 1;
    
    pr_info("诊断：宽限期可能卡住\n");
    pr_info("  GP seq: %lu, state: %d, idx: %d\n",
            gp_seq, rcu_seq_state(gp_seq), idx);
    
    // 检查每个 CPU 的读取者
    for_each_possible_cpu(cpu) {
        struct srcu_data *sdp = per_cpu_ptr(ssp->sda, cpu);
        unsigned long locks = atomic_long_read(&sdp->srcu_ctrs[idx].srcu_locks);
        unsigned long unlocks = atomic_long_read(&sdp->srcu_ctrs[idx].srcu_unlocks);
        long active = locks - unlocks;
        
        if (active > 0) {
            pr_info("  CPU %d: locks=%lu, unlocks=%lu, active=%ld\n",
                    cpu, locks, unlocks, active);
            
            // 检查是否有任务持有锁
            // 注意：这需要额外的调试信息
        }
    }
}
```

#### 解决方案

1. **检查长时间运行的读取者**：
```c
// 添加超时机制
static bool synchronize_srcu_timeout(struct srcu_struct *ssp, 
                                     unsigned long timeout_jiffies)
{
    unsigned long start = jiffies;
    unsigned long cookie = start_poll_synchronize_srcu(ssp);
    
    while (!poll_state_synchronize_srcu(ssp, cookie)) {
        if (time_after(jiffies, start + timeout_jiffies)) {
            pr_warn("synchronize_srcu 超时！\n");
            diagnose_gp_stuck(ssp);
            return false;
        }
        cond_resched();
    }
    return true;
}
```

2. **使用 expedited 版本**：
```c
// 使用快速版本（但可能影响性能）
synchronize_srcu_expedited(ssp);
```

### 问题 3: 内存泄漏

#### 症状
- 系统内存逐渐减少
- `srcu_struct` 结构未正确清理

#### 诊断代码

```c
static void check_srcu_cleanup(struct srcu_struct *ssp)
{
    struct srcu_usage *sup = ssp->srcu_sup;
    
    // 检查是否有未完成的宽限期
    if (READ_ONCE(sup->srcu_gp_seq_needed) != 
        READ_ONCE(sup->srcu_gp_seq)) {
        pr_warn("警告：有未完成的宽限期请求\n");
    }
    
    // 检查是否有待处理的回调
    // ...
}
```

#### 解决方案

```c
// 确保正确清理
void cleanup_module(void)
{
    // 1. 等待所有宽限期完成
    synchronize_srcu(&my_srcu);
    
    // 2. 清理结构
    cleanup_srcu_struct(&my_srcu);
    
    // 3. 释放内存
    // ...
}
```

### 问题 4: 竞争条件

#### 症状
- 数据不一致
- 偶尔崩溃
- 难以重现

#### 诊断代码

```c
// 使用 WARN_ON 检查竞争条件
void my_srcu_reader(struct srcu_struct *ssp)
{
    int idx = srcu_read_lock(ssp);
    
    // 检查数据一致性
    struct my_data *data = srcu_dereference(ptr, ssp);
    WARN_ON(!data, "数据指针为空！\n");
    WARN_ON(data->magic != EXPECTED_MAGIC, 
            "数据魔数错误：%x\n", data->magic);
    
    // ...
    
    srcu_read_unlock(ssp, idx);
}
```

#### 解决方案

1. **使用正确的内存屏障**：
```c
// 确保正确的内存顺序
void update_data(struct srcu_struct *ssp)
{
    struct my_data *new_data = kmalloc(sizeof(*new_data), GFP_KERNEL);
    
    // 初始化新数据
    new_data->magic = EXPECTED_MAGIC;
    // ...
    
    // 使用 rcu_assign_pointer 更新指针
    rcu_assign_pointer(ptr, new_data);
    
    // 等待宽限期后清理旧数据
    synchronize_srcu(ssp);
    // ...
}
```

2. **使用 KASAN 检测**：
```bash
# 编译时启用 KASAN
CONFIG_KASAN=y
CONFIG_KASAN_INLINE=y
```

---

## 性能分析

### 1. 测量宽限期延迟

```c
#include <linux/ktime.h>

static void measure_gp_latency(struct srcu_struct *ssp)
{
    ktime_t start, end;
    s64 latency_ns;
    
    start = ktime_get();
    synchronize_srcu(ssp);
    end = ktime_get();
    
    latency_ns = ktime_to_ns(ktime_sub(end, start));
    pr_info("SRCU 宽限期延迟: %lld ns (%lld us)\n",
            latency_ns, latency_ns / 1000);
}
```

### 2. 统计读取者活动

```c
static void collect_reader_stats(struct srcu_struct *ssp)
{
    int cpu;
    int idx;
    struct srcu_stats {
        unsigned long total_locks;
        unsigned long total_unlocks;
        unsigned long max_active;
    } stats[2] = { {0}, {0} };
    
    for (idx = 0; idx < 2; idx++) {
        for_each_possible_cpu(cpu) {
            struct srcu_data *sdp = per_cpu_ptr(ssp->sda, cpu);
            unsigned long locks = atomic_long_read(&sdp->srcu_ctrs[idx].srcu_locks);
            unsigned long unlocks = atomic_long_read(&sdp->srcu_ctrs[idx].srcu_unlocks);
            long active = locks - unlocks;
            
            stats[idx].total_locks += locks;
            stats[idx].total_unlocks += unlocks;
            if (active > stats[idx].max_active)
                stats[idx].max_active = active;
        }
    }
    
    pr_info("SRCU 统计:\n");
    for (idx = 0; idx < 2; idx++) {
        pr_info("  idx %d: locks=%lu, unlocks=%lu, max_active=%lu\n",
                idx, stats[idx].total_locks, stats[idx].total_unlocks,
                stats[idx].max_active);
    }
}
```

### 3. 分析竞争情况

```c
static atomic_t contention_count = ATOMIC_INIT(0);

// 在锁获取失败时调用
static void record_contention(struct srcu_struct *ssp)
{
    atomic_inc(&contention_count);
    
    if (atomic_read(&contention_count) % 100 == 0) {
        pr_warn("SRCU 竞争计数: %d\n", 
                atomic_read(&contention_count));
    }
}
```

---

## 实战案例

### 案例 1: 调试死锁问题

#### 场景
系统在某个操作后挂起，怀疑是 SRCU 死锁。

#### 调试步骤

```c
// 1. 添加检查点
static void debug_checkpoint(const char *name, struct srcu_struct *ssp)
{
    pr_info("检查点 %s: ", name);
    if (srcu_read_lock_held(ssp)) {
        pr_cont("在读取端\n");
        dump_stack();
    } else {
        pr_cont("不在读取端\n");
    }
}

// 2. 在关键位置添加检查
void suspicious_function(struct srcu_struct *ssp)
{
    debug_checkpoint("函数入口", ssp);
    
    int idx = srcu_read_lock(ssp);
    debug_checkpoint("获取锁后", ssp);
    
    // 可疑操作
    another_function(ssp);  // 可能在这里调用 synchronize_srcu
    
    srcu_read_unlock(ssp, idx);
    debug_checkpoint("释放锁后", ssp);
}
```

### 案例 2: 优化宽限期性能

#### 场景
`synchronize_srcu()` 执行时间过长，影响系统性能。

#### 优化方案

```c
// 1. 使用异步回调代替同步等待
static void async_update(struct srcu_struct *ssp, void *data)
{
    // 使用 call_srcu 异步处理
    call_srcu(ssp, &my_rcu_head, cleanup_callback);
}

// 2. 批量处理更新
static void batch_update(struct srcu_struct *ssp)
{
    // 收集多个更新
    // 一次性调用 synchronize_srcu
    synchronize_srcu(ssp);
    // 处理所有更新
}

// 3. 使用 expedited 版本（谨慎使用）
void fast_update(struct srcu_struct *ssp)
{
    // 只在必要时使用
    if (urgent_update_needed)
        synchronize_srcu_expedited(ssp);
    else
        synchronize_srcu(ssp);
}
```

### 案例 3: 检测内存泄漏

#### 场景
长时间运行后系统内存减少，怀疑 SRCU 相关内存泄漏。

#### 检测代码

```c
// 跟踪 SRCU 结构分配和释放
static atomic_t srcu_struct_count = ATOMIC_INIT(0);

int init_srcu_struct(struct srcu_struct *ssp)
{
    int ret = __init_srcu_struct(ssp);
    if (!ret) {
        atomic_inc(&srcu_struct_count);
        pr_debug("SRCU 结构分配: 总数=%d\n",
                 atomic_read(&srcu_struct_count));
    }
    return ret;
}

void cleanup_srcu_struct(struct srcu_struct *ssp)
{
    __cleanup_srcu_struct(ssp);
    atomic_dec(&srcu_struct_count);
    pr_debug("SRCU 结构释放: 剩余=%d\n",
             atomic_read(&srcu_struct_count));
}

// 定期检查
static void check_srcu_leak(void)
{
    int count = atomic_read(&srcu_struct_count);
    if (count > expected_count) {
        pr_warn("可能的 SRCU 内存泄漏: 当前计数=%d\n", count);
    }
}
```

---

## 调试检查清单

### 初始化检查
- [ ] 是否正确调用 `init_srcu_struct()`？
- [ ] 静态初始化是否正确？
- [ ] 是否有足够的内存？

### 使用检查
- [ ] `srcu_read_lock()` 和 `srcu_read_unlock()` 是否配对？
- [ ] 是否在读取端调用了 `synchronize_srcu()`？
- [ ] 索引是否正确传递？
- [ ] 是否在中断中错误使用了普通版本？

### 清理检查
- [ ] 是否调用了 `cleanup_srcu_struct()`？
- [ ] 是否等待所有宽限期完成？
- [ ] 是否释放了所有相关内存？

### 性能检查
- [ ] 宽限期是否过长？
- [ ] 是否有过多的竞争？
- [ ] 是否可以使用更轻量的同步机制？

---

## 总结

SRCU 调试需要：
1. **理解机制**：掌握 SRCU 的工作原理
2. **使用工具**：充分利用内核调试工具
3. **添加检查**：在关键位置添加诊断代码
4. **分析数据**：收集和分析性能数据
5. **系统方法**：按照检查清单逐步排查

记住：大多数 SRCU 问题都是由于在读取端调用 `synchronize_srcu()` 导致的死锁！

---

*最后更新: 2024*

# SRCU 学习资源索引

欢迎学习 SRCU (Sleepable Read-Copy-Update)！本目录包含完整的学习材料。

## 📚 文档列表

### 1. [SRCU_学习指南.md](./SRCU_学习指南.md)
**适合人群**：初学者和需要全面了解的开发者

**内容概览**：
- ✅ SRCU 概述和核心特性
- ✅ 架构设计（Tree 和 Tiny 版本）
- ✅ 执行流程详解
- ✅ 调试方法
- ✅ 代码示例
- ✅ 常见问题解答

**推荐阅读顺序**：
1. 先阅读"概述"了解基本概念
2. 学习"架构设计"理解数据结构
3. 掌握"执行流程"了解工作原理
4. 参考"代码示例"进行实践

---

### 2. [SRCU_调试实战.md](./SRCU_调试实战.md)
**适合人群**：需要调试 SRCU 问题的开发者

**内容概览**：
- 🔧 调试工具和技巧（printk, ftrace, kprobes 等）
- 🔍 常见问题诊断（死锁、宽限期卡住、内存泄漏等）
- 📊 性能分析方法
- 💡 实战案例

**推荐使用场景**：
- 遇到 SRCU 相关问题时查阅
- 需要添加调试代码时参考
- 性能优化时使用

---

### 3. [SRCU_代码深度分析.md](./SRCU_代码深度分析.md)
**适合人群**：需要深入理解实现的开发者

**内容概览**：
- 🔬 核心函数详细分析
- 🧠 内存屏障详解和配对关系
- 🔄 宽限期状态机
- 🌳 组合树机制
- ⚡ 性能优化技巧

**推荐阅读场景**：
- 需要修改 SRCU 代码时
- 深入理解内存模型时
- 优化性能时

---

## 🚀 快速开始

### 新手入门路径

1. **第一步**：阅读 [SRCU_学习指南.md](./SRCU_学习指南.md) 的"概述"部分
   - 了解什么是 SRCU
   - 理解与 RCU 的区别
   - 知道何时使用 SRCU

2. **第二步**：学习基本使用
   - 查看"代码示例"部分
   - 理解 `srcu_read_lock()` 和 `srcu_read_unlock()` 的配对
   - 掌握 `synchronize_srcu()` 的使用

3. **第三步**：理解架构
   - 学习"架构设计"部分
   - 理解两阶段宽限期机制
   - 了解数据结构层次

4. **第四步**：实践和调试
   - 参考"调试方法"部分
   - 遇到问题时查阅"常见问题"
   - 使用调试工具验证理解

### 进阶学习路径

1. **深入理解**：阅读 [SRCU_代码深度分析.md](./SRCU_代码深度分析.md)
   - 理解内存屏障的作用
   - 掌握状态机转换
   - 学习组合树机制

2. **实战调试**：参考 [SRCU_调试实战.md](./SRCU_调试实战.md)
   - 学习调试技巧
   - 分析常见问题
   - 优化性能

3. **源码阅读**：结合内核源码
   - `include/linux/srcu.h` - 接口定义
   - `kernel/rcu/srcutree.c` - Tree 实现
   - `kernel/rcu/srcutiny.c` - Tiny 实现

---

## 📖 核心概念速查

### 基本 API

```c
// 初始化
DEFINE_SRCU(name);                    // 静态初始化
init_srcu_struct(&ssp);               // 动态初始化

// 读取端
int idx = srcu_read_lock(&ssp);       // 加锁
srcu_read_unlock(&ssp, idx);          // 解锁

// 更新端
synchronize_srcu(&ssp);               // 同步等待
call_srcu(&ssp, &rhp, func);          // 异步回调

// 清理
cleanup_srcu_struct(&ssp);            // 清理结构
```

### 关键数据结构

- `srcu_struct` - 顶层结构
- `srcu_data` - 每 CPU 数据
- `srcu_node` - 组合树节点
- `srcu_usage` - 更新端数据

### 宽限期状态

- `SRCU_STATE_IDLE` - 空闲
- `SRCU_STATE_SCAN1` - 第一次扫描
- `SRCU_STATE_SCAN2` - 第二次扫描

---

## ⚠️ 重要注意事项

### 禁止的操作

1. ❌ **在读取端调用 `synchronize_srcu()`** - 会导致死锁
2. ❌ **在中断中使用普通 `srcu_read_lock()`** - 使用 NMI-safe 版本
3. ❌ **忘记配对 `srcu_read_lock()` 和 `srcu_read_unlock()`**
4. ❌ **在清理前不等待宽限期完成**

### 最佳实践

1. ✅ **保持读取端临界区尽可能短**
2. ✅ **使用 `srcu_dereference()` 访问受保护的数据**
3. ✅ **批量处理更新操作**
4. ✅ **使用异步回调避免阻塞**
5. ✅ **正确清理 SRCU 结构**

---

## 🔗 相关资源

### 内核源码位置

- **头文件**: `include/linux/srcu.h`
- **Tree 实现**: `kernel/rcu/srcutree.c`
- **Tiny 实现**: `kernel/rcu/srcutiny.c`
- **Tree 头文件**: `include/linux/srcutree.h`
- **Tiny 头文件**: `include/linux/srcutiny.h`

### 内核文档

- `Documentation/RCU/` - RCU 相关文档
- `Documentation/RCU/srcu.txt` - SRCU 文档（如果存在）

### 测试工具

- `tools/testing/selftests/rcutorture/` - 压力测试
- `kernel/rcu/rcutorture.c` - 测试框架

---

## 📝 学习检查清单

### 基础理解
- [ ] 理解 SRCU 与 RCU 的区别
- [ ] 知道何时使用 SRCU
- [ ] 掌握基本 API 的使用
- [ ] 理解两阶段宽限期机制

### 深入理解
- [ ] 理解内存屏障的作用和配对
- [ ] 掌握宽限期状态机
- [ ] 了解组合树机制
- [ ] 理解索引切换的原理

### 实践能力
- [ ] 能够正确使用 SRCU API
- [ ] 能够调试常见问题
- [ ] 能够优化性能
- [ ] 能够阅读和理解源码

---

## 🎯 学习目标

完成这些文档的学习后，你应该能够：

1. **理解** SRCU 的工作原理和设计思想
2. **使用** SRCU API 编写正确的代码
3. **调试** SRCU 相关的问题
4. **优化** SRCU 的性能
5. **阅读** SRCU 的内核源码

---

## 💬 反馈和建议

如果你发现文档中的错误或有改进建议，欢迎反馈！

---

**祝学习愉快！** 🚀

*最后更新: 2024*

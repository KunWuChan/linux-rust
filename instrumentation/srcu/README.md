SRCU Instrumentation Bundle
===========================

Contents
--------

- bpftrace_srcu.bt: Full bpftrace script logging key SRCU updates.
- bpftrace_srcu_min.bt: Minimal bpftrace focused on two core events.
- tracepoints_srcu.patch: Optional kernel tracepoints header to add structured events.
- gdb_guide.txt: GDB observation flow and confirmation checklist.

Usage - bpftrace
----------------

Prereqs: CONFIG_KPROBES, CONFIG_BPF, CONFIG_DEBUG_INFO_BTF=y, bpftrace installed.

Run full script:

  sudo bpftrace /workspace/instrumentation/srcu/bpftrace_srcu.bt

Run minimal script:

  sudo bpftrace /workspace/instrumentation/srcu/bpftrace_srcu_min.bt

Expected key observations:

- srcu_gp_end: sup->srcu_gp_seq_needed_exp >= current gp_seq.
- srcu_funnel_gp_start: normal path raises sup->srcu_gp_seq_needed; expedited also raises sup->srcu_gp_seq_needed_exp.
- srcu_funnel_exp_start: per-node snp->srcu_gp_seq_needed_exp bumps along the path.

Usage - tracepoints (optional)
------------------------------

Apply header into include/trace/events/srcu.h, add to build, and rebuild kernel:

  git apply /workspace/instrumentation/srcu/tracepoints_srcu.patch
  # include the new header from kernel/rcu/srcutree.c as needed and TRACE_INCLUDE_PATH
  # then rebuild and boot the kernel

Record:

  sudo trace-cmd record -e srcu:srcu_sup_needed -e srcu:srcu_node_needed_exp -e srcu:srcu_sdp_needed
  sudo trace-cmd report


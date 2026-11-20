  // link guarantees (HOSTIF 9, D077, D107; obligations of the link proof): EXEC only to a stopped engine without a
  // pending injection, RUN and STEP never while an injection is pending, one pulse kind per clock
  always @(*) assume (!exec_p || (!run && !xm));
  always @(*) assume (!(run_p || step_p) || !xm);
  always @(*) assume ({1'b0, run_p} + stop_p + step_p + exec_p <= 3'd1);

`default_nettype none
// Properties of jspe_fifo (VERIF_PLAN 6, ARCH 2.4, D046); fifo.sby exposes v, m1-m3 and sets W per task (FV_W9)
`ifdef FV_W9
`define FV_W 9
`else
`define FV_W 8
`endif
module fv_fifo #(
    parameter W = `FV_W
) (
    input wire         clk,
    input wire         wr,
    input wire [W-1:0] wdata,
    input wire         rd,
    input wire         flush,
    input wire         trk
);
  wire [W-1:0] rdata, m1, m2, m3;
  wire         empty, full;
  wire [2:0]   free;
  wire [3:0]   v;

  jspe_fifo dut (
      .clk  (clk),
      .wr   (wr),
      .wdata(wdata),
      .rd   (rd),
      .rdata(rdata),
      .empty(empty),
      .full (full),
      .free (free),
      .flush(flush),
      .v    (v),
      .m1   (m1),
      .m2   (m2),
      .m3   (m3)
  );

  // flush (engine reset) in the first clock; checks start after it
  reg f_past = 1'b0, f2 = 1'b0;
  always @(posedge clk) begin
    f_past <= 1'b1;
    f2     <= f_past;
  end
  always @(*) if (!f_past) assume (flush);

  wire [2:0]     cnt  = v[0] + v[1] + v[2] + v[3];
  wire           push = wr && cnt != 3'd4;
  wire           pop  = rd && cnt != 3'd0;
  wire [4*W-1:0] ent  = {m3, m2, m1, rdata};

  // previous clock
  reg           flush_q, push_q, pop_q, wr_q, rd_q;
  reg [2:0]     cnt_q;
  reg [W-1:0]   wd_q;
  reg [4*W-1:0] ent_q;
  always @(posedge clk) begin
    flush_q <= flush;
    push_q  <= push;
    pop_q   <= pop;
    wr_q    <= wr;
    rd_q    <= rd;
    cnt_q   <= cnt;
    wd_q    <= wdata;
    ent_q   <= ent;
  end

  // tracked word: any pushed word (free choice), with the number of entries still ahead of it
  reg         tr = 1'b0;
  reg [W-1:0] tdat;
  reg [1:0]   ahead, ahead0;
  always @(posedge clk) begin
    if (flush) tr <= 1'b0;
    else if (!tr && trk && push) begin
      tr     <= 1'b1;
      tdat   <= wdata;
      ahead  <= cnt - pop;
      ahead0 <= cnt - pop;
    end else if (tr && pop) begin
      if (ahead == 2'd0) tr <= 1'b0;
      else ahead <= ahead - 2'd1;
    end
  end
  wire [W-1:0] t_ent = ent[W*ahead +: W];

  always @(*) if (f_past) begin
    a_therm:  assert (v == 4'b0000 || v == 4'b0001 || v == 4'b0011 || v == 4'b0111 || v == 4'b1111); // valid entries form a thermometer from the head (count <= 4)
    a_lvl:    assert (empty == (cnt == 3'd0) && full == (cnt == 3'd4) && free == 3'd4 - cnt); // empty, full, free agree with the count
    a_cnt:    assert (flush_q || cnt == cnt_q + push_q - pop_q); // count + push - pop, limits on the registered levels (D046)
    a_wfull:  assert (flush_q || cnt_q != 3'd4 || (pop_q ? ent[3*W-1:0] == ent_q[4*W-1:W] : ent == ent_q)); // a write to a full FIFO is ignored, also with a pop in that clock
    a_rempty: assert (flush_q || cnt_q != 3'd0 || (wr_q ? (v == 4'b0001 && rdata == wd_q) : v == 4'b0000)); // a pop of an empty FIFO is ignored, also with a write in that clock
    a_flush:  assert (!flush_q || v == 4'b0000); // flush empties the FIFO
    a_trk:    assert (!tr || ahead != 2'd0 || (!empty && rdata == tdat)); // a pushed word is the head after as many pops as entries were ahead of it
    h_tpos:   assert (!tr || (v[ahead] && t_ent == tdat)); // the tracked word sits at entry `ahead`
  end

  always @(*) if (f_past) begin
    c_therm:    cover (v == 4'b1111);
    c_lvl:      cover (free == 3'd1 && !full);
    c_cnt:      cover (!flush_q && push_q && pop_q && cnt == 3'd2);
    c_wfull:    cover (!flush_q && cnt_q == 3'd4 && wr_q && rd_q && cnt == 3'd3);
    c_wfull_np: cover (!flush_q && cnt_q == 3'd4 && wr_q && !rd_q);
    c_rempty:   cover (!flush_q && cnt_q == 3'd0 && wr_q && rd_q && !empty);
    c_flush:    cover (f2 && flush_q && cnt_q == 3'd4);
    c_trk:      cover (tr && ahead == 2'd0 && ahead0 == 2'd3 && pop && push);
    c_trk_full: cover (tr && ahead == 2'd0 && pop && cnt == 3'd4);
    c_tpos:     cover (tr && ahead == 2'd3);
  end
endmodule

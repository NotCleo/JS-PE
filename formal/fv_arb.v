`default_nettype none
// Properties of jspe_arb (VERIF_PLAN 6, ARCH 2.2, ISA 10 slot terms); `slot` is exposed by arb.sby
module fv_arb (
    input wire        clk,
    input wire        rst_n,
    input wire [7:0]  slotmap,
    input wire [3:0]  e_req,
    input wire [3:0]  e_we,
    input wire [35:0] e_addr,
    input wire [63:0] e_wdata,
    input wire        h_req,
    input wire        h_we,
    input wire [8:0]  h_addr,
    input wire [15:0] h_wdata
);
  wire [3:0]  e_gnt;
  wire        h_gnt, mem_en, mem_we;
  wire [8:0]  mem_addr;
  wire [15:0] mem_wdata;
  wire [1:0]  slot;

  jspe_arb dut (
      .clk      (clk),
      .rst_n    (rst_n),
      .slotmap  (slotmap),
      .e_req    (e_req),
      .e_we     (e_we),
      .e_addr   (e_addr),
      .e_wdata  (e_wdata),
      .h_req    (h_req),
      .h_we     (h_we),
      .h_addr   (h_addr),
      .h_wdata  (h_wdata),
      .e_gnt    (e_gnt),
      .h_gnt    (h_gnt),
      .mem_en   (mem_en),
      .mem_we   (mem_we),
      .mem_addr (mem_addr),
      .mem_wdata(mem_wdata),
      .slot     (slot)
  );

  // reset in the first clock; checks start after it
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (!rst_n);

  // spec model: slot 0 at reset, +1 every clock; owner = SLOTMAP[2s+1:2s]
  reg  [1:0] m_slot;
  reg        wrap_q;
  always @(posedge clk) begin
    m_slot <= rst_n ? m_slot + 2'd1 : 2'd0;
    wrap_q <= rst_n && m_slot == 2'd3;
  end
  wire [1:0] owner = slotmap[{m_slot, 1'b0} +: 2];
  wire [3:0] own1h = 4'b0001 << owner;
  wire       e_any = |e_gnt;
  wire [4:0] gnts  = {h_gnt, e_gnt};
  wire       one0  = (gnts & (gnts - 5'd1)) == 5'd0;
  wire [3:0] others = e_req & ~own1h;

  always @(*) if (f_past) begin
    a_slot:  assert (slot == m_slot);                   // slot = free-running 2-bit counter, 0 at reset
    a_egnt:  assert (e_gnt == (e_req & own1h));         // e_gnt[i] <=> e_req[i] and slot owner = i
    a_one:   assert (one0);                             // at most one grant among engines and host
    a_hgnt:  assert (h_gnt == (h_req & ~e_req[owner])); // host granted <=> owner not requesting
    a_en:    assert (mem_en == (e_any | h_gnt));        // mem_en <=> a grant
    a_we0:   assert (mem_en | ~mem_we);                 // no write without a grant
    a_mux_h: assert (!h_gnt || (mem_we == h_we && mem_addr == h_addr && mem_wdata == h_wdata)); // host grant: host's access
  end

  always @(*) if (f_past) begin
    c_slot:  cover (wrap_q && slot == 2'd0);
    c_egnt:  cover (e_any && h_req && others != 4'd0);
    c_one:   cover (e_req[owner] && h_req && others != 4'd0);
    c_hgnt:  cover (h_gnt && others != 4'd0);
    c_hgnt_blk: cover (h_req && !h_gnt);
    c_en:    cover ((|e_req) && !h_req && !mem_en);
    c_we0:   cover (!mem_en && (|(e_req & e_we)));
    c_mux_h: cover (h_gnt && h_we);
  end

  // per engine: access mux and slot wait bound
  reg [7:0] sm_q;
  always @(posedge clk) sm_q <= slotmap;
  genvar i;
  generate
    for (i = 0; i < 4; i = i + 1) begin : g_e
      fv_arb_eng #(.E(i)) u (
          .clk      (clk),
          .rst_n    (rst_n),
          .f_past   (f_past),
          .slotmap  (slotmap),
          .sm_q     (sm_q),
          .req      (e_req[i]),
          .gnt      (e_gnt[i]),
          .we       (e_we[i]),
          .addr     (e_addr[9*i +: 9]),
          .wdata    (e_wdata[16*i +: 16]),
          .h_req    (h_req),
          .mem_we   (mem_we),
          .mem_addr (mem_addr),
          .mem_wdata(mem_wdata)
      );
    end
  endgenerate
endmodule

// one engine: its access through the mux, and the wait bound G - 1 of ISA 10 while SLOTMAP is unchanged
module fv_arb_eng #(
    parameter E = 0
) (
    input wire        clk,
    input wire        rst_n,
    input wire        f_past,
    input wire [7:0]  slotmap,
    input wire [7:0]  sm_q,
    input wire        req,
    input wire        gnt,
    input wire        we,
    input wire [8:0]  addr,
    input wire [15:0] wdata,
    input wire        h_req,
    input wire        mem_we,
    input wire [8:0]  mem_addr,
    input wire [15:0] mem_wdata
);
`include "fv_gap.vh"

  reg  [2:0] run_q;
  wire [2:0] g = gap(slotmap, E);
  wire [2:0] run_now = (slotmap != sm_q) ? 3'd1 : (run_q == 3'd7) ? 3'd7 : run_q + 3'd1;
  wire       waiting = req & ~gnt;
  always @(posedge clk) run_q <= (rst_n && waiting) ? run_now : 3'd0;

  always @(*) if (f_past) begin
    a_mux_e: assert (!gnt || (mem_we == we && mem_addr == addr && mem_wdata == wdata)); // engine grant: its access
    a_wait:  assert (!waiting || g == 3'd0 || run_now < g); // held request granted within G - 1 clk
    c_mux_e: cover (gnt && we && h_req);
    c_wait:  cover (gnt && g == 3'd4 && run_q == 3'd3 && slotmap == 8'hE4);
    c_wait_g: cover (gnt && g != 3'd4 && g != 3'd0 && run_q == g - 3'd1 && run_q != 3'd0);
  end
endmodule

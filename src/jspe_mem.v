`default_nettype none
// Program/table memory (ARCH 2.3): 128x16 flops (D062 build), or the 512x16 SRAM macro with JSPE_MEM_SRAM.
module jspe_mem (
    input  wire        clk,
    input  wire        en,
    input  wire        we,
    input  wire [8:0]  addr,
    input  wire [15:0] wdata,
    output wire [15:0] rdata
);
`ifndef JSPE_MEM_SRAM
  reg [15:0] mem [0:127];
  reg [6:0]  raddr;
  always @(posedge clk) begin
    if (en & we) mem[addr[6:0]] <= wdata;
    if (en & ~we) raddr <= addr[6:0];
  end
  assign rdata = mem[raddr];
  wire _unused = &{1'b0, addr[8:7]};
`else
  RM_IHPSG13_1P_512x16_c2_bm_bist u_sram (
      .A_CLK      (clk),
      .A_MEN      (en),
      .A_WEN      (we),
      .A_REN      (~we),
      .A_ADDR     (addr),
      .A_DIN      (wdata),
      .A_DLY      (1'b1),
      .A_DOUT     (rdata),
      .A_BM       (16'hFFFF),
      .A_BIST_CLK (1'b0),
      .A_BIST_EN  (1'b0),
      .A_BIST_MEN (1'b0),
      .A_BIST_WEN (1'b0),
      .A_BIST_REN (1'b0),
      .A_BIST_ADDR(9'd0),
      .A_BIST_DIN (16'd0),
      .A_BIST_BM  (16'd0)
  );
`endif
endmodule

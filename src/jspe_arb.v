`default_nettype none
// Memory port arbiter: strict time division by slot (ARCH 2.2, D018, D027)
module jspe_arb (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  slotmap,
    input  wire [3:0]  e_req,
    input  wire [3:0]  e_we,
    input  wire [35:0] e_addr,
    input  wire [63:0] e_wdata,
    input  wire        h_req,
    input  wire        h_we,
    input  wire [8:0]  h_addr,
    input  wire [15:0] h_wdata,
    output wire [3:0]  e_gnt,
    output wire        h_gnt,
    output wire        mem_en,
    output wire        mem_we,
    output wire [8:0]  mem_addr,
    output wire [15:0] mem_wdata
);
  localparam AW = 9;
  localparam DW = 16;

  reg [1:0] slot;
  always @(posedge clk) begin
    if (!rst_n) slot <= 2'd0;
    else slot <= slot + 2'd1;
  end

  wire [1:0] own = slotmap[{slot, 1'b0} +: 2];
  wire [3:0] own1h = 4'b0001 << own;

  wire e_any = |e_gnt;
  assign e_gnt = e_req & own1h;
  assign h_gnt = h_req & ~e_any;
  assign mem_en = e_any | h_req;

  // one-hot AND-OR, 0 when nothing is granted
  assign mem_we = (|(e_gnt & e_we)) | (h_gnt & h_we);
  assign mem_addr = ({AW{e_gnt[0]}} & e_addr[0*AW+:AW]) | ({AW{e_gnt[1]}} & e_addr[1*AW+:AW])
                  | ({AW{e_gnt[2]}} & e_addr[2*AW+:AW]) | ({AW{e_gnt[3]}} & e_addr[3*AW+:AW])
                  | ({AW{h_gnt}} & h_addr);
  assign mem_wdata = ({DW{e_gnt[0]}} & e_wdata[0*DW+:DW]) | ({DW{e_gnt[1]}} & e_wdata[1*DW+:DW])
                   | ({DW{e_gnt[2]}} & e_wdata[2*DW+:DW]) | ({DW{e_gnt[3]}} & e_wdata[3*DW+:DW])
                   | ({DW{h_gnt}} & h_wdata);
endmodule

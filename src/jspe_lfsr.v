`default_nettype none
// Shared 32-bit LFSR with programmable polynomial, left or right form (ARCH 2.7, D013)
module jspe_lfsr (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [3:0]  e_step,
    input  wire [3:0]  e_bit,
    input  wire [3:0]  e_we,
    input  wire [11:0] e_idx,
    input  wire [63:0] e_wdata,
    output reg  [31:0] lfsr
);
  localparam IW = 3;
  localparam DW = 16;

  reg [31:0] poly;
  reg        right;

  // lowest engine wins, one-hot
  wire [3:0] wsel = e_we & {~|e_we[2:0], ~|e_we[1:0], ~e_we[0], 1'b1};
  wire [3:0] ssel = e_step & {~|e_step[2:0], ~|e_step[1:0], ~e_step[0], 1'b1};
  wire       we = |e_we;
  wire [IW-1:0] idx = ({IW{wsel[0]}} & e_idx[0*IW+:IW]) | ({IW{wsel[1]}} & e_idx[1*IW+:IW])
                    | ({IW{wsel[2]}} & e_idx[2*IW+:IW]) | ({IW{wsel[3]}} & e_idx[3*IW+:IW]);
  wire [DW-1:0] wd = ({DW{wsel[0]}} & e_wdata[0*DW+:DW]) | ({DW{wsel[1]}} & e_wdata[1*DW+:DW])
                   | ({DW{wsel[2]}} & e_wdata[2*DW+:DW]) | ({DW{wsel[3]}} & e_wdata[3*DW+:DW]);

  // idx is 0 when nobody writes; 0-3 = LFSR_LO, LFSR_HI, POLY_LO, POLY_HI, 4 = LFCFG
  wire [1:0] w_l = {2{we & ~idx[2] & ~idx[1]}} & {idx[0], ~idx[0]};
  wire [1:0] w_p = {2{~idx[2] & idx[1]}} & {idx[0], ~idx[0]};
  wire       w_c = idx[2] & ~idx[1] & ~idx[0];

  // a write wins over a step
  wire step = |e_step & ~we;
  wire sbit = |(ssel & e_bit);
  wire fb = step & (sbit ^ (right ? lfsr[0] : lfsr[31]));
  wire sl = step & ~right;
  wire sr = step & right;

  wire [31:0] wl32 = {{16{w_l[1]}}, {16{w_l[0]}}};
  wire [31:0] sh = ({32{sl}} & {lfsr[30:0], 1'b0}) | ({32{sr}} & {1'b0, lfsr[31:1]})
                 | (~{32{step}} & ~wl32 & lfsr) | (wl32 & {wd, wd});
  wire [31:0] nx = sh ^ ({32{fb}} & poly);

  always @(posedge clk) begin
    lfsr <= nx;
    if (w_p[0]) poly[15:0] <= wd;
    if (w_p[1]) poly[31:16] <= wd;
  end

  always @(posedge clk) begin
    if (!rst_n) right <= 1'b0;
    else if (w_c) right <= wd[0];
  end
endmodule

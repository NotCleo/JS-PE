`default_nettype none
// Engine registers R0-R2, operand muxes, ALU, shared adder for the memory address and write-back (ARCH 3.1, D068).
module jspe_alu (
    input  wire        clk,
    input  wire [15:0] ir,
    input  wire        fire,
    input  wire        ld_done,
    input  wire        st_done,
    input  wire [15:0] s,
    input  wire [15:0] p,
    input  wire [15:0] c,
    input  wire [15:0] t,
    input  wire [8:0]  l,
    input  wire        cy,
    input  wire [7:0]  rx_rdata,
    input  wire        rx_empty,
    input  wire [31:0] lfsr,
    input  wire [7:0]  sig,
    input  wire [15:0] mem_rdata,
    output reg  [15:0] opb,
    output wire        wr_en,
    output wire [2:0]  wr_reg,
    output wire [15:0] wr_data,
    output wire        wb_en,
    output wire [1:0]  wb_reg,
    output wire [15:0] wb_data,
    output wire [8:0]  maddr,
    output wire        z_n,
    output wire        cy_n,
    output wire        flag_we
);
/* verilator lint_off UNUSEDPARAM */
`include "jspe_isa.vh"
/* verilator lint_on UNUSEDPARAM */

  reg [15:0] r0, r1, r2;

  wire is_gm   = (ir[15:12] == 4'hD);
  wire is_im   = (ir[15:12] == 4'hE);
  wire is_rx   = (ir[15:12] == 4'hF);
  wire is_mov  = is_gm && (ir[11:10] == 2'b00);
  wire is_pull = is_gm && (ir[11:10] == 2'b01) && ir[8];
  wire is_alu  = is_rx && (ir[11:10] == 2'b00);
  wire is_mem  = is_rx && (ir[11:9] == 3'b100);
  wire is_rcfg = is_rx && (ir[11:8] == 4'hB);
  wire [3:0] op   = ir[9:6];
  wire [1:0] iop  = ir[11:10];
  wire [4:0] creg = ir[7:3];

  // operand A: rd (ALU, IM, pullh) or the memory base; operand B: rs
  wire [2:0] a_sel = is_mem ? {1'b0, ir[4:3]} : is_im ? {1'b0, ir[9:8]} : is_alu ? ir[5:3] : ir[6:4];
  wire [2:0] b_sel = is_gm ? ir[6:4] : is_mem ? ir[7:5] : ir[2:0];
  reg [15:0] opa;
  always @(*) begin
    case (a_sel)
      REG_R0:  opa = r0;
      REG_R1:  opa = r1;
      REG_R2:  opa = r2;
      REG_S:   opa = s;
      REG_P:   opa = p;
      REG_C:   opa = c;
      REG_T:   opa = t;
      default: opa = {7'd0, l};
    endcase
    case (b_sel)
      REG_R0:  opb = r0;
      REG_R1:  opb = r1;
      REG_R2:  opb = r2;
      REG_S:   opb = s;
      REG_P:   opb = p;
      REG_C:   opb = c;
      REG_T:   opb = t;
      default: opb = {7'd0, l};
    endcase
  end

  // one adder: ALU add/adc/sub/cmp, IM add/cmp, rb + 0/1/S
  wire sub = is_alu ? (op == ALU_SUB || op == ALU_CMP) : (is_im && iop == IMM_CMP);
  wire cin = sub | (is_alu && op == ALU_ADC && cy);
  wire [15:0] add_b = is_mem ? (ir[2] ? s : {15'd0, ir[1]}) :
                      is_im  ? {{8{ir[7] && iop == IMM_ADD}}, ir[7:0]} : opb;
  wire [16:0] sum17 = {1'b0, opa} + {1'b0, add_b ^ {16{sub}}} + {16'd0, cin};
  wire [15:0] sum = sum17[15:0];
  wire        co  = sum17[16];

  reg [15:0] ares;
  reg        acy;
  always @(*) begin
    ares = sum;
    acy  = co;
    case (op)
      ALU_AND, ALU_TST: begin ares = opa & opb; acy = cy; end
      ALU_OR:  begin ares = opa | opb; acy = cy; end
      ALU_XOR: begin ares = opa ^ opb; acy = cy; end
      ALU_SHL: begin ares = {opb[14:0], 1'b0}; acy = opb[15]; end
      ALU_RLC: begin ares = {opb[14:0], cy}; acy = opb[15]; end
      ALU_SHR: begin ares = {1'b0, opb[15:1]}; acy = opb[0]; end
      ALU_RRC: begin ares = {cy, opb[15:1]}; acy = opb[0]; end
      default: ;
    endcase
  end
  assign z_n     = is_alu ? (ares == 16'd0) : (sum == 16'd0);
  assign cy_n    = is_alu ? acy : co;
  assign flag_we = fire && (is_alu || (is_im && iop[1]));

  // byte forms: li / lih, pull / pullh
  wire [7:0]  bytev = is_im ? ir[7:0] : rx_rdata;
  wire        hi    = is_im ? iop[0] : ir[7];
  wire [15:0] bres  = hi ? {bytev, opa[7:0]} : {8'd0, bytev};
  wire [15:0] cval  = (creg == CREG_LFSR_LO) ? lfsr[15:0] : (creg == CREG_LFSR_HI) ? lfsr[31:16] :
                      (creg == CREG_SIG) ? {8'd0, sig} : 16'd0;

  assign wr_data = ld_done ? mem_rdata : is_alu ? ares : is_im ? (iop[1] ? sum : bres) :
                   is_mov ? opb : is_pull ? bres : cval;
  assign wr_reg  = ld_done ? ir[7:5] : is_alu ? ir[5:3] : is_im ? {1'b0, ir[9:8]} :
                   is_mov ? ir[9:7] : is_pull ? ir[6:4] : ir[2:0];
  wire   wr_fire = is_alu ? (op != ALU_CMP && op != ALU_TST) : is_im ? (iop != IMM_CMP) :
                   is_mov ? 1'b1 : is_pull ? ~rx_empty : is_rcfg;
  assign wr_en   = ld_done | (fire & wr_fire);
  assign wb_en   = (ld_done | st_done) & ir[1];
  assign wb_reg  = ir[4:3];
  assign wb_data = sum;
  assign maddr   = sum[8:0];

  // write-back first, so the loaded value wins when rd == rb
  always @(posedge clk) begin
    if (wb_en && wb_reg == REG2_R0) r0 <= wb_data;
    if (wr_en && wr_reg == REG_R0)  r0 <= wr_data;
    if (wb_en && wb_reg == REG2_R1) r1 <= wb_data;
    if (wr_en && wr_reg == REG_R1)  r1 <= wr_data;
    if (wb_en && wb_reg == REG2_R2) r2 <= wb_data;
    if (wr_en && wr_reg == REG_R2)  r2 <= wr_data;
  end
endmodule

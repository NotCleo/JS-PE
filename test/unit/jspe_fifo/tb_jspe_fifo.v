`default_nettype none
// both FIFO widths in one elaboration (RX 4x8, TX 4x9)
module tb_jspe_fifo (
    input  wire       clk,
    input  wire       wr8,
    input  wire [7:0] wdata8,
    input  wire       rd8,
    input  wire       flush8,
    output wire [7:0] rdata8,
    output wire       empty8,
    output wire       full8,
    output wire [2:0] free8,
    input  wire       wr9,
    input  wire [8:0] wdata9,
    input  wire       rd9,
    input  wire       flush9,
    output wire [8:0] rdata9,
    output wire       empty9,
    output wire       full9,
    output wire [2:0] free9
);
  jspe_fifo #(.W(8)) u8 (
      .clk  (clk),
      .wr   (wr8),
      .wdata(wdata8),
      .rd   (rd8),
      .rdata(rdata8),
      .empty(empty8),
      .full (full8),
      .free (free8),
      .flush(flush8)
  );
  jspe_fifo #(.W(9)) u9 (
      .clk  (clk),
      .wr   (wr9),
      .wdata(wdata9),
      .rd   (rd9),
      .rdata(rdata9),
      .empty(empty9),
      .full (full9),
      .free (free9),
      .flush(flush9)
  );
endmodule

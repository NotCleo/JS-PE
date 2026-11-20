/*
 * Copyright (c) 2026 NotCleo
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none
// JS-PE top (ARCH 1): pins, host link, arbiter, memory, 4 engines with RX/TX FIFOs, signal flags, LFSR.
module tt_um_notcleo_jspe (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  reg [1:0] rst_s;
  always @(posedge clk) rst_s <= {rst_s[0], rst_n};
  wire rst_i = rst_s[1];

  // pins
  wire [15:0] lvl, lvl_raw, e_val, e_oe, filt;
  wire        pad_we;
  wire [3:0]  pad_idx;
  wire [6:0]  pad_wdata;
  wire        h_tx, h_rdy;
  // link
  wire [3:0]  l_rx_wr, l_rx_ovf, l_tx_rd, l_run, l_stop, l_step, l_rst, l_exec;
  wire [7:0]  l_rx_wdata, l_slotmap, l_e_clr, l_sig_wdata;
  wire [15:0] l_exec_word;
  wire        l_sig_we;
  wire        h_req, h_we, h_gnt;
  wire [8:0]  h_addr;
  wire [15:0] h_wdata;
  // FIFOs (engine e at the low-first slice)
  wire [11:0] rx_free;
  wire [35:0] tx_rdata;
  wire [3:0]  tx_empty, tx_full, rx_empty, rx_full;
  wire [31:0] rx_rdata;
  wire [11:0] tx_free;
  // engines
  wire [3:0]  e_req, e_we, e_gnt, e_rx_rd, e_tx_wr, e_ovr, e_halt, e_trap, e_xm;
  wire [3:0]  e_lf_step, e_lf_bit, e_lf_we;
  wire [35:0] e_addr, e_tx_wdata;
  wire [63:0] e_wdata, e_stat, e_lf_wdata;
  wire [31:0] e_sig_set, e_sig_clr;
  wire [11:0] e_lf_idx;
  wire [3:0]  eng_rst = {4{~rst_i}} | l_rst;
  // shared
  wire [7:0]  sig;
  wire [31:0] lfsr;
  wire        mem_en, mem_we;
  wire [8:0]  mem_addr;
  wire [15:0] mem_wdata, mem_rdata;

  jspe_pins u_pins (
      .clk      (clk),
      .rst_n    (rst_i),
      .ui_in    (ui_in),
      .uio_in   (uio_in),
      .lvl      (lvl),
      .lvl_raw  (lvl_raw),
      .filt     (filt),
      .e_val    (e_val),
      .e_oe     (e_oe),
      .pad_we   (pad_we),
      .pad_idx  (pad_idx),
      .pad_wdata(pad_wdata),
      .h_tx     (h_tx),
      .h_rdy    (h_rdy),
      .uo_out   (uo_out),
      .uio_out  (uio_out),
      .uio_oe   (uio_oe)
  );

  jspe_link u_link (
      .clk      (clk),
      .rst_n    (rst_i),
      .h_rx_s   (lvl_raw[3]),
      .h_tx     (h_tx),
      .h_rdy    (h_rdy),
      .rx_wr    (l_rx_wr),
      .rx_wdata (l_rx_wdata),
      .rx_free  (rx_free),
      .rx_ovf   (l_rx_ovf),
      .tx_rd    (l_tx_rd),
      .tx_rdata (tx_rdata),
      .tx_empty (tx_empty),
      .e_run    (l_run),
      .e_stop   (l_stop),
      .e_step   (l_step),
      .e_rst    (l_rst),
      .e_exec   (l_exec),
      .exec_word(l_exec_word),
      .e_stat   (e_stat),
      .e_ovr    (e_ovr),
      .e_halt   (e_halt),
      .e_trap   (e_trap),
      .e_xm     (e_xm),
      .e_clr    (l_e_clr),
      .slotmap  (l_slotmap),
      .filt     (filt),
      .pad_we   (pad_we),
      .pad_idx  (pad_idx),
      .pad_wdata(pad_wdata),
      .sig      (sig),
      .sig_we   (l_sig_we),
      .sig_wdata(l_sig_wdata),
      .lvl      (lvl_raw),
      .h_req    (h_req),
      .h_we     (h_we),
      .h_addr   (h_addr),
      .h_wdata  (h_wdata),
      .h_gnt    (h_gnt),
      .mem_rdata(mem_rdata)
  );

  genvar e;
  generate
    for (e = 0; e < 4; e = e + 1) begin : g_eng
      jspe_fifo #(.W(8)) u_rx (
          .clk  (clk),
          .wr   (l_rx_wr[e]),
          .wdata(l_rx_wdata),
          .rd   (e_rx_rd[e]),
          .rdata(rx_rdata[8*e +: 8]),
          .empty(rx_empty[e]),
          .full (rx_full[e]),
          .free (rx_free[3*e +: 3]),
          .flush(eng_rst[e])
      );

      jspe_fifo #(.W(9)) u_tx (
          .clk  (clk),
          .wr   (e_tx_wr[e]),
          .wdata(e_tx_wdata[9*e +: 9]),
          .rd   (l_tx_rd[e]),
          .rdata(tx_rdata[9*e +: 9]),
          .empty(tx_empty[e]),
          .full (tx_full[e]),
          .free (tx_free[3*e +: 3]),
          .flush(eng_rst[e])
      );

      jspe_engine u_eng (
          .clk      (clk),
          .rst      (eng_rst[e]),
          .run      (l_run[e]),
          .stop     (l_stop[e]),
          .step     (l_step[e]),
          .exec     (l_exec[e]),
          .exec_word(l_exec_word),
          .lvl      (lvl),
          .sig      (sig),
          .req      (e_req[e]),
          .we       (e_we[e]),
          .addr     (e_addr[9*e +: 9]),
          .wdata    (e_wdata[16*e +: 16]),
          .gnt      (e_gnt[e]),
          .mem_rdata(mem_rdata),
          .rx_rd    (e_rx_rd[e]),
          .rx_rdata (rx_rdata[8*e +: 8]),
          .rx_empty (rx_empty[e]),
          .rx_ovf   (l_rx_ovf[e]),
          .tx_wr    (e_tx_wr[e]),
          .tx_wdata (e_tx_wdata[9*e +: 9]),
          .tx_full  (tx_full[e]),
          .tx_empty (tx_empty[e]),
          .val      (e_val[4*e +: 4]),
          .oe       (e_oe[4*e +: 4]),
          .sig_set  (e_sig_set[8*e +: 8]),
          .sig_clr  (e_sig_clr[8*e +: 8]),
          .lf_step  (e_lf_step[e]),
          .lf_bit   (e_lf_bit[e]),
          .lf_we    (e_lf_we[e]),
          .lf_idx   (e_lf_idx[3*e +: 3]),
          .lf_wdata (e_lf_wdata[16*e +: 16]),
          .lfsr     (lfsr),
          .stat     (e_stat[16*e +: 16]),
          .ovr      (e_ovr[e]),
          .halted   (e_halt[e]),
          .trap     (e_trap[e]),
          .xm       (e_xm[e]),
          .clr_ovr  (l_e_clr[e]),
          .clr_ht   (l_e_clr[4+e])
      );
    end
  endgenerate

  jspe_arb u_arb (
      .clk      (clk),
      .rst_n    (rst_i),
      .slotmap  (l_slotmap),
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
      .mem_wdata(mem_wdata)
  );

  jspe_mem u_mem (
      .clk  (clk),
      .en   (mem_en),
      .we   (mem_we),
      .addr (mem_addr),
      .wdata(mem_wdata),
      .rdata(mem_rdata)
  );

  jspe_sig u_sig (
      .clk      (clk),
      .rst_n    (rst_i),
      .e_set    (e_sig_set),
      .e_clr    (e_sig_clr),
      .sig_we   (l_sig_we),
      .sig_wdata(l_sig_wdata),
      .sig      (sig)
  );

  jspe_lfsr u_lfsr (
      .clk    (clk),
      .rst_n  (rst_i),
      .e_step (e_lf_step),
      .e_bit  (e_lf_bit),
      .e_we   (e_lf_we),
      .e_idx  (e_lf_idx),
      .e_wdata(e_lf_wdata),
      .lfsr   (lfsr)
  );

  wire _unused = &{1'b0, ena, rx_full, tx_free};
endmodule

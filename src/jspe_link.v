`default_nettype none
// Host link (ARCH 2.5, HOSTIF.md, D077, D078): 8N1 receiver, parser, host registers, memory client, transmitter.
module jspe_link (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        h_rx_s,
    output reg         h_tx,
    output reg         h_rdy,
    output wire [3:0]  rx_wr,
    output wire [7:0]  rx_wdata,
    input  wire [11:0] rx_free,
    output wire [3:0]  rx_ovf,
    output wire [3:0]  tx_rd,
    input  wire [35:0] tx_rdata,
    input  wire [3:0]  tx_empty,
    output wire [3:0]  e_run,
    output wire [3:0]  e_stop,
    output wire [3:0]  e_step,
    output wire [3:0]  e_rst,
    output wire [3:0]  e_exec,
    output wire [15:0] exec_word,
    input  wire [63:0] e_stat,
    input  wire [3:0]  e_ovr,
    input  wire [3:0]  e_halt,
    input  wire [3:0]  e_trap,
    input  wire [3:0]  e_xm,
    output wire [7:0]  e_clr,
    output reg  [7:0]  slotmap,
    output reg  [15:0] filt,
    output wire        pad_we,
    output wire [3:0]  pad_idx,
    output wire [6:0]  pad_wdata,
    input  wire [7:0]  sig,
    output wire        sig_we,
    output wire [7:0]  sig_wdata,
    input  wire [15:0] lvl,
    output wire        h_req,
    output wire        h_we,
    output wire [8:0]  h_addr,
    output wire [15:0] h_wdata,
    input  wire        h_gnt,
    input  wire [15:0] mem_rdata
);
/* verilator lint_off UNUSEDPARAM */
`include "jspe_isa.vh"
/* verilator lint_on UNUSEDPARAM */

  // bit time from LINK[1:0]: 16, 8, 4 (3 = 16); stored as B - 1
  reg  [1:0] rate;
  wire [1:0] rate_n;
  function [3:0] bm1;
    input [1:0] r;
    bm1 = (r == 2'd1) ? 4'd7 : (r == 2'd2) ? 4'd3 : 4'd15;
  endfunction
  wire [3:0] btm1 = bm1(rate_n);

  // ---------------- receiver ----------------
  localparam [1:0] RX_IDLE = 2'd0, RX_BITS = 2'd1, RX_WAIT = 2'd2;
  reg [1:0] rx_st, rx_st_n;
  reg [1:0] rx_rt;
  reg [3:0] rx_cnt, rx_i;
  wire [3:0] rx_bm1 = bm1(rx_rt);
  reg [7:0] rx_sh;
  wire rx_smp  = (rx_st == RX_BITS) && (rx_cnt == 4'd0);
  wire rx_stop = rx_smp && (rx_i == 4'd9);
  wire b_vld   = rx_stop && h_rx_s;
  wire rx_ferr = rx_stop && !h_rx_s;
  wire rx_brk  = rx_ferr && (rx_sh == 8'd0);
  wire [7:0] b = rx_sh;

  always @(*) begin
    rx_st_n = rx_st;
    case (rx_st)
      RX_IDLE: if (!h_rx_s) rx_st_n = RX_BITS;
      RX_BITS: if (rx_smp && ((rx_i == 4'd0 && h_rx_s) || rx_i == 4'd9))
                 rx_st_n = (rx_i == 4'd9 && !h_rx_s) ? RX_WAIT : RX_IDLE;
      RX_WAIT: if (h_rx_s) rx_st_n = RX_IDLE;
      default: rx_st_n = RX_IDLE;
    endcase
  end

  always @(posedge clk) begin
    if (!rst_n) begin
      rx_st  <= RX_IDLE;
      rx_cnt <= 4'd0;
      rx_rt  <= 2'd0;
      rx_i   <= 4'd0;
    end else begin
      rx_st <= rx_st_n;
      if (rx_st == RX_IDLE && !h_rx_s) begin
        rx_rt  <= rate_n;
        rx_cnt <= btm1 >> 1;
        rx_i   <= 4'd0;
      end else if (rx_st == RX_BITS) begin
        if (rx_smp) begin
          rx_cnt <= rx_bm1;
          rx_i   <= rx_i + 4'd1;
        end else begin
          rx_cnt <= rx_cnt - 4'd1;
        end
      end
    end
  end

  always @(posedge clk)
    if (rx_smp && rx_i != 4'd0 && rx_i != 4'd9) rx_sh <= {h_rx_s, rx_sh[7:1]};

  // ---------------- parser ----------------
  localparam [1:0] P_DATA = 2'd0, P_OP = 2'd1, P_ARGS = 2'd2, P_WRD = 2'd3;
  reg [1:0] p_st, p_st_n;
  reg [7:0] op, a0, a1;
  reg [1:0] need, argi;
  reg       sel_v, wr_lo_v;
  reg [1:0] sel;
  reg [8:0] wr_addr, wr_left;
  reg [2:0] pk;
  reg [3:0] pm;

  wire op_n2 = (b[7:2] == 6'b011000);
  wire op_n3 = (b == H2C_WRMEM) || (b == H2C_RDMEM) || (b == H2C_WRREG);
  wire op_n1 = (b == H2C_RDREG);
  wire op_eng = (b[7:5] == 3'b001) || (b[7:5] == 3'b010);
  wire op_n0 = (b[7:2] == 6'd0) || op_eng || (b == H2C_SYNC);
  wire op_ok = op_n0 | op_n1 | op_n2 | op_n3;
  wire args_done = (p_st == P_ARGS) && ({argi + 2'd1} == need);
  wire cmd0 = b_vld && (p_st == P_OP) && op_n0;
  wire cmd  = b_vld && args_done;

  // command strobes, in the clock of the stop sample of the last byte
  wire       do_sync  = cmd0 && (b == H2C_SYNC);
  wire       do_sel   = cmd0 && (b[7:2] == 6'd0);
  wire       do_engop = cmd0 && op_eng;
  wire       do_exec  = cmd && (op[7:2] == 6'b011000);
  wire       do_wrmem = cmd && (op == H2C_WRMEM);
  wire       do_rdmem = cmd && (op == H2C_RDMEM);
  wire       do_wrreg = cmd && (op == H2C_WRREG);
  wire       do_rdreg = cmd && (op == H2C_RDREG);
  wire       wr_word  = b_vld && (p_st == P_WRD) && wr_lo_v;
  wire [3:0] m        = b[3:0];
  wire [3:0] op_hi    = b[7:4];
  wire [3:0] ex_oh    = 4'b0001 << op[1:0];
  wire       busy_any = |({e_stat[57], e_stat[41], e_stat[25], e_stat[9]} | e_xm);
  wire [3:0] e_runs   = {e_stat[57], e_stat[41], e_stat[25], e_stat[9]};
  wire       ex_bad   = do_exec && |(ex_oh & (e_runs | e_xm));
  wire       rs_bad   = do_engop && (op_hi == 4'h3 || op_hi == 4'h4) && |(m & e_xm);
  wire       sm_bad   = do_wrreg && (a0 == HREG_SLOTMAP) && busy_any;
  // a new bit time applies to a byte that starts in this clock (either direction)
  assign rate_n = (do_wrreg && a0 == HREG_LINK) ? a1[1:0] : rate;

  always @(*) begin
    p_st_n = p_st;
    if (rx_brk) p_st_n = P_DATA;
    else if (b_vld) begin
      case (p_st)
        P_DATA: if (b == H_ESC) p_st_n = P_OP;
        P_OP:   p_st_n = (op_ok && !op_n0 && b != H_ESC) ? P_ARGS : P_DATA;
        P_ARGS: if (args_done) p_st_n = (op == H2C_WRMEM) ? P_WRD : P_DATA;
        P_WRD:  if (wr_lo_v && wr_left == 9'd1) p_st_n = P_DATA;
        default: p_st_n = P_DATA;
      endcase
    end
  end

  wire data_byte = b_vld && (((p_st == P_DATA) && b != H_ESC) || ((p_st == P_OP) && b == H_ESC));
  wire op_bad    = b_vld && (p_st == P_OP) && b != H_ESC && !op_ok;
  wire sel_v_n   = sel_v | do_sel;
  wire [1:0] sel_n = do_sel ? b[1:0] : sel;

  // stream data and host register strobes (combinational, the receiving blocks register them)
  wire [3:0] sel_oh = 4'b0001 << sel;
  wire [3:0] rx_full = {rx_free[11:9] == 3'd0, rx_free[8:6] == 3'd0, rx_free[5:3] == 3'd0, rx_free[2:0] == 3'd0};
  assign rx_wr     = {4{data_byte & sel_v}} & sel_oh;
  assign rx_wdata  = b;
  assign rx_ovf    = rx_wr & rx_full;
  assign e_clr     = {8{do_wrreg && a0 == HREG_ECLR}} & a1;
  assign sig_we    = do_wrreg && (a0 == HREG_SIG);
  assign sig_wdata = a1;
  assign pad_we    = do_wrreg && (a0[7:4] == HREG_PAD_UO[7:4]) && (a0[3:1] != 3'b010);
  assign pad_idx   = a0[3:0];
  assign pad_wdata = a1[6:0];

  // memory write pending (one word)
  reg        wr_pend;
  reg [8:0]  wr_paddr;
  reg [15:0] wr_pdata;
  wire wr_lost = wr_word && wr_pend && !(h_gnt && h_we);

  always @(posedge clk) begin
    if (!rst_n) begin
      p_st    <= P_DATA;
      need    <= 2'd0;
      argi    <= 2'd0;
      sel_v   <= 1'b0;
      sel     <= 2'd0;
      wr_lo_v <= 1'b0;
      wr_left <= 9'd0;
      wr_addr <= 9'd0;
      rate    <= 2'd0;
      slotmap <= HREG_SLOTMAP_RESET[7:0];
      filt    <= 16'h0000;
      wr_pend <= 1'b0;
      pk      <= 3'd0;
    end else begin
      p_st  <= p_st_n;
      sel_v <= sel_v_n;
      sel   <= sel_n;
      if (b_vld && p_st == P_OP) begin
        need <= op_n2 ? 2'd2 : op_n3 ? 2'd3 : 2'd1;
        argi <= 2'd0;
      end
      if (b_vld && p_st == P_ARGS) argi <= argi + 2'd1;
      if (do_wrmem) begin
        wr_addr <= {a1[0], a0};
        wr_left <= {1'b0, b} + 9'd1;
        wr_lo_v <= 1'b0;
      end
      if (b_vld && p_st == P_WRD) begin
        wr_lo_v <= ~wr_lo_v;
        if (wr_lo_v) begin
          wr_addr <= wr_addr + 9'd1;
          wr_left <= wr_left - 9'd1;
        end
      end
      rate <= rate_n;
      if (do_wrreg && a0 == HREG_SLOTMAP && !busy_any) slotmap <= a1;
      if (do_wrreg && a0 == HREG_FILT) filt <= {b, a1};
      if (wr_word) wr_pend <= 1'b1;
      else if (h_gnt && h_we) wr_pend <= 1'b0;
      pk <= do_engop ? op_hi[2:0] : (do_exec && !ex_bad) ? 3'b110 : 3'b000;
    end
  end

  always @(posedge clk) begin
    if (b_vld && p_st == P_OP) op <= b;
    if (b_vld && p_st == P_ARGS && argi == 2'd0) a0 <= b;
    if (b_vld && p_st == P_ARGS && argi == 2'd1) a1 <= b;
    if (b_vld && p_st == P_WRD && !wr_lo_v) a0 <= b;      // a0 holds the low byte of a WRMEM word
    if (wr_word) begin
      wr_paddr <= wr_addr;
      wr_pdata <= {b, a0};
    end
    pm <= do_engop ? (m & ((op_hi == 4'h3 || op_hi == 4'h4) ? ~e_xm : 4'hF)) : ex_oh;
  end

  // engine pulses, one clock after the stop sample: kind = op[6:4] (2 stop, 3 run, 4 step, 5 rst, 6 exec)
  assign e_stop    = {4{pk == 3'd2}} & pm;
  assign e_run     = {4{pk == 3'd3}} & pm;
  assign e_step    = {4{pk == 3'd4}} & pm;
  assign e_rst     = {4{pk == 3'd5}} & pm;
  assign e_exec    = {4{pk == 3'd6}} & pm;
  assign exec_word = {b, a0};       // unchanged until the next byte's first data sample

  // ---------------- reply (RDMEM / RDREG / SYNC), one RDMEM word buffered ----------------
  reg        rp_act, rp_have, rp_hi, rd_fl;
  reg [1:0]  rp_hdr;
  reg [1:0]  rp_kind;
  reg [8:0]  rd_left, rd_addr;
  wire [7:0] rp_code = rp_kind[1] ? C2H_RDMEM : rp_kind[0] ? C2H_RDREG : C2H_SYNC;
  reg [15:0] rp_word;

  reg [15:0] reg_rd;
  always @(*) begin
    case (b)
      HREG_ID:      reg_rd = HREG_ID_RESET;
      HREG_LINK:    reg_rd = {14'd0, rate};
      HREG_SLOTMAP: reg_rd = {8'd0, slotmap};
      HREG_SIG:     reg_rd = {8'd0, sig};
      HREG_PINS:    reg_rd = lvl;
      HREG_EST0:    reg_rd = e_stat[15:0];
      HREG_EST1:    reg_rd = e_stat[31:16];
      HREG_EST2:    reg_rd = e_stat[47:32];
      HREG_EST3:    reg_rd = e_stat[63:48];
      default:      reg_rd = 16'd0;
    endcase
  end

  wire rp_new = do_rdmem | do_rdreg | do_sync;
  wire rd_req = !rp_have && !rd_fl && (rd_left != 9'd0);
  assign h_req   = wr_pend | rd_req;
  assign h_we    = wr_pend;
  assign h_addr  = wr_pend ? wr_paddr : rd_addr;
  assign h_wdata = wr_pdata;
  wire rd_gnt = h_gnt && !wr_pend && rd_req;

  // ---------------- notices: pending flags, lowest code first ----------------
  reg  [11:0] fl_prev;
  reg  [12:0] nt;
  wire [11:0] fl_now = {e_trap, e_halt, e_ovr};
  wire        err    = rx_ferr | (data_byte & ~sel_v) | op_bad | wr_lost | ex_bad | rs_bad | sm_bad;
  wire [12:0] nt_all = nt | {err, fl_now & ~fl_prev};
  wire [12:0] nt_low = nt_all & (~nt_all + 13'd1);
  reg  [3:0]  nt_code;
  integer k;
  always @(*) begin
    nt_code = 4'd0;
    for (k = 12; k >= 0; k = k - 1)
      if (nt_all[k]) nt_code = k[3:0];
  end

  // ---------------- stream scheduler ----------------
  reg        cur_v;
  reg [1:0]  cur;
  reg [2:0]  served;
  wire [3:0] wait_s = ~tx_empty;
  wire [3:0] cur_oh = 4'b0001 << cur;
  wire       sw_need = !cur_v || !(|(wait_s & cur_oh)) || (served[2] && |(wait_s & ~cur_oh));
  // round robin: first waiting engine above cur, else the lowest waiting
  wire [3:0] above = cur_v ? (wait_s & ~((cur_oh << 1) - 4'd1)) : wait_s;
  wire [3:0] pool  = (|above) ? above : wait_s;
  wire [1:0] nxt   = pool[0] ? 2'd0 : pool[1] ? 2'd1 : pool[2] ? 2'd2 : 2'd3;
  wire [8:0] ent   = (cur == 2'd0) ? tx_rdata[8:0] : (cur == 2'd1) ? tx_rdata[17:9] :
                     (cur == 2'd2) ? tx_rdata[26:18] : tx_rdata[35:27];

  // ---------------- transmitter ----------------
  reg       tx_busy, b2v;
  reg [3:0] tx_n, tx_q;
  reg [1:0] tx_rt;
  wire [3:0] tx_bm1 = bm1(tx_rt);
  reg [8:0] tx_sh;
  reg [7:0] b2;

  reg        tx_ld, ld_b2, take_rp, take_nt, take_st, sw_st;
  reg  [7:0] tx_byte, b2_n;
  always @(*) begin
    tx_ld   = 1'b0;
    ld_b2   = 1'b0;
    take_rp = 1'b0;
    take_nt = 1'b0;
    take_st = 1'b0;
    sw_st   = 1'b0;
    tx_byte = H_ESC;
    b2_n    = H_ESC;
    if (!tx_busy) begin
      if (b2v) begin
        tx_ld   = 1'b1;
        tx_byte = b2;
        ld_b2   = 1'b1;
      end else if (rp_new) begin
        tx_ld   = 1'b1;
        take_rp = 1'b1;
      end else if (rp_act) begin
        if (rp_hdr != 2'd0 || rp_have) begin
          tx_ld   = 1'b1;
          take_rp = 1'b1;
          tx_byte = (rp_hdr == 2'd2) ? H_ESC : (rp_hdr == 2'd1) ? rp_code :
                    rp_hi ? rp_word[15:8] : rp_word[7:0];
        end
      end else if (|nt_all) begin
        tx_ld   = 1'b1;
        take_nt = 1'b1;
        b2_n    = C2H_OVR | {4'd0, nt_code};
      end else if (|wait_s) begin
        tx_ld = 1'b1;
        if (sw_need) begin
          sw_st = 1'b1;
          b2_n  = {6'd0, nxt};
        end else begin
          take_st = 1'b1;
          if (ent[8]) b2_n = C2H_EVENT | {2'b00, ent[5:0]};
          else if (ent[7:0] != H_ESC) tx_byte = ent[7:0];
        end
      end
    end
  end
  wire b2_set = take_nt | sw_st | (take_st & (ent[8] | ent[7:0] == H_ESC));
  assign tx_rd = {4{take_st}} & cur_oh;

  wire tx_bit = (tx_n == 4'd0) ? 1'b0 : tx_sh[0];
  wire tx_txd = tx_ld ? 1'b0 : tx_busy ? tx_bit : 1'b1;
  // data state or after an ESC (the next byte may be a literal), D080
  wire h_rdy_n = (p_st_n == P_DATA || p_st_n == P_OP) && sel_v_n &&
                 ((sel_n == 2'd0) ? rx_free[2:1] == 2'd0 : (sel_n == 2'd1) ? rx_free[5:4] == 2'd0 :
                  (sel_n == 2'd2) ? rx_free[8:7] == 2'd0 : rx_free[11:10] == 2'd0);

  always @(posedge clk) begin
    if (!rst_n) begin
      rp_act  <= 1'b0;
      rp_have <= 1'b0;
      rp_hi   <= 1'b0;
      rp_hdr  <= 2'd0;
      rd_left <= 9'd0;
      rd_fl   <= 1'b0;
      fl_prev <= 12'd0;
      nt      <= 13'd0;
      cur_v   <= 1'b0;
      cur     <= 2'd0;
      served  <= 3'd0;
      tx_busy <= 1'b0;
      b2v     <= 1'b0;
      tx_n    <= 4'd0;
      tx_q    <= 4'd0;
      tx_rt   <= 2'd0;
      h_tx    <= 1'b1;
      h_rdy   <= 1'b0;
    end else begin
      h_tx    <= tx_txd;
      h_rdy   <= h_rdy_n;
      fl_prev <= fl_now;
      nt      <= nt_all & ~({13{take_nt}} & nt_low);
      // memory reads for RDMEM
      rd_fl <= rd_gnt;
      if (rd_gnt) rd_left <= rd_left - 9'd1;
      if (rd_fl) rp_have <= 1'b1;
      if (take_rp) begin
        if (rp_hdr != 2'd0) rp_hdr <= rp_hdr - 2'd1;
        else begin
          rp_hi <= ~rp_hi;
          if (rp_hi) begin
            rp_have <= 1'b0;
            if (rd_left == 9'd0) rp_act <= 1'b0;
          end
        end
      end
      if (rp_new) begin
        rp_act  <= 1'b1;
        rp_hdr  <= take_rp ? 2'd1 : 2'd2;
        rp_hi   <= 1'b0;
        rp_have <= !do_rdmem;
        rd_left <= do_rdmem ? ({1'b0, b} + 9'd1) : 9'd0;
        rd_fl   <= 1'b0;
      end
      // streams
      if (sw_st) begin
        cur_v  <= 1'b1;
        cur    <= nxt;
        served <= 3'd0;
      end else if (take_st && !served[2]) begin
        served <= served + 3'd1;
      end
      // second byte of an escape sequence
      if (ld_b2) b2v <= 1'b0;
      else if (b2_set) b2v <= 1'b1;
      // UART transmit, 8N1, B clk per bit
      if (tx_ld) begin
        tx_busy <= 1'b1;
        tx_n    <= 4'd0;
        tx_q    <= 4'd1;
        tx_rt   <= rate_n;
      end else if (tx_busy) begin
        if (tx_q == tx_bm1) begin
          tx_q <= 4'd0;
          if (tx_n == 4'd9) tx_busy <= 1'b0;
          tx_n <= tx_n + 4'd1;
        end else begin
          tx_q <= tx_q + 4'd1;
        end
      end
    end
  end

  always @(posedge clk) begin
    if (tx_ld) tx_sh <= {1'b1, tx_byte};
    else if (tx_busy && tx_q == tx_bm1 && tx_n != 4'd0) tx_sh <= {1'b1, tx_sh[8:1]};
    if (b2_set) b2 <= b2_n;
    if (rp_new) begin
      rp_kind <= {do_rdmem, do_rdreg};
      rp_word <= do_sync ? HREG_ID_RESET : reg_rd;
      rd_addr <= {a1[0], a0};
    end else begin
      if (rd_gnt) rd_addr <= rd_addr + 9'd1;
      if (rd_fl) rp_word <= mem_rdata;
    end
  end

  wire _unused = pool[3];
endmodule

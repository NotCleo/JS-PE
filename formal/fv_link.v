`default_nettype none
// Properties of jspe_link (VERIF_PLAN 6, HOSTIF, D077, D080, D107, D114); link.sby exposes internals; tasks cover and bmc cut the
// receiver to the byte level (LINK_CUT: a byte every other clock at most) so commands are reached in a few clocks
module fv_link (
    input wire        clk,
    input wire        rst_n,
    input wire        h_rx_s,
    input wire [11:0] rx_free,
    input wire [35:0] tx_rdata,
    input wire [3:0]  tx_empty,
    input wire [63:0] e_stat,
    input wire [3:0]  e_ovr,
    input wire [3:0]  e_halt,
    input wire [3:0]  e_trap,
    input wire [3:0]  e_xm,
    input wire [7:0]  sig,
    input wire [15:0] lvl,
    input wire        h_gnt,
    input wire [15:0] mem_rdata
);
  wire        h_tx, h_rdy, pad_we, sig_we, h_req, h_we;
  wire [3:0]  rx_wr, rx_ovf, tx_rd, e_run, e_stop, e_step, e_rst, e_exec, pad_idx;
  wire [7:0]  rx_wdata, e_clr, slotmap, sig_wdata;
  wire [15:0] exec_word, filt, h_wdata;
  wire [6:0]  pad_wdata;
  wire [8:0]  h_addr;
  wire [1:0]  rx_st;
  wire [2:0]  pk;
  wire        b_vld, rx_brk, sel_v, wr_lo_v;
  wire [7:0]  b, op, a0, a1;
  wire [1:0]  p_st, need, argi, sel;
  wire [8:0]  wr_left, wr_addr;
  wire        tx_ld, take_nt, sw_st, take_rp, cur_v, b2v, rp_act;
  wire [7:0]  tx_byte, b2_n, rp_code, b2;
  wire [1:0]  rp_hdr, cur;

  jspe_link dut (
      .clk      (clk),
      .rst_n    (rst_n),
      .h_rx_s   (h_rx_s),
      .h_tx     (h_tx),
      .h_rdy    (h_rdy),
      .rx_wr    (rx_wr),
      .rx_wdata (rx_wdata),
      .rx_free  (rx_free),
      .rx_ovf   (rx_ovf),
      .tx_rd    (tx_rd),
      .tx_rdata (tx_rdata),
      .tx_empty (tx_empty),
      .e_run    (e_run),
      .e_stop   (e_stop),
      .e_step   (e_step),
      .e_rst    (e_rst),
      .e_exec   (e_exec),
      .exec_word(exec_word),
      .e_stat   (e_stat),
      .e_ovr    (e_ovr),
      .e_halt   (e_halt),
      .e_trap   (e_trap),
      .e_xm     (e_xm),
      .e_clr    (e_clr),
      .slotmap  (slotmap),
      .filt     (filt),
      .pad_we   (pad_we),
      .pad_idx  (pad_idx),
      .pad_wdata(pad_wdata),
      .sig      (sig),
      .sig_we   (sig_we),
      .sig_wdata(sig_wdata),
      .lvl      (lvl),
      .h_req    (h_req),
      .h_we     (h_we),
      .h_addr   (h_addr),
      .h_wdata  (h_wdata),
      .h_gnt    (h_gnt),
      .mem_rdata(mem_rdata),
      .rx_st    (rx_st),
      .pk       (pk),
      .b_vld    (b_vld),
      .b        (b),
      .rx_brk   (rx_brk),
      .p_st     (p_st),
      .need     (need),
      .argi     (argi),
      .op       (op),
      .a0       (a0),
      .a1       (a1),
      .sel_v    (sel_v),
      .sel      (sel),
      .wr_left  (wr_left),
      .wr_lo_v  (wr_lo_v),
      .wr_addr  (wr_addr),
      .tx_ld    (tx_ld),
      .tx_byte  (tx_byte),
      .b2_n     (b2_n),
      .take_nt  (take_nt),
      .sw_st    (sw_st),
      .take_rp  (take_rp),
      .rp_hdr   (rp_hdr),
      .rp_code  (rp_code),
      .cur      (cur),
      .cur_v    (cur_v),
      .b2v      (b2v),
      .b2       (b2),
      .rp_act   (rp_act)
  );

  // reset in the first clock; checks start after it
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (!rst_n);

  // engine and arbiter side, only as proven without the link environment: run rises only after RUN/STEP (seq a_run_up),
  // xm only after EXEC (seq a_xm_up), host grant only with a host request (arb a_hgnt)
  wire [3:0] e_runs = {e_stat[57], e_stat[41], e_stat[25], e_stat[9]};
  reg  [3:0] runs_q, xm_q, rs_q, ex_q;
  reg        bv_q;
  always @(posedge clk) begin
    runs_q <= e_runs;
    xm_q   <= e_xm;
    rs_q   <= e_run | e_step;
    ex_q   <= e_exec;
    bv_q   <= b_vld;
  end
  always @(*) if (f_past) begin
    assume ((e_runs & ~runs_q & ~rs_q) == 4'h0);
    assume ((e_xm & ~xm_q & ~ex_q) == 4'h0);
  end
  always @(*) assume (!h_gnt || h_req);
`ifdef LINK_CUT
  // as the real receiver: no stop sample in the clock after one, the byte register holds in that clock
  reg [7:0] b_q;
  always @(posedge clk) b_q <= b;
  always @(*) assume (!(b_vld && bv_q));
  always @(*) if (f_past) assume (!bv_q || b == b_q);
`endif

  // at most one pulse kind per engine
  reg       one_kind;
  reg [4:0] kv;
  integer   e;
  always @(*) begin
    one_kind = 1'b1;
    for (e = 0; e < 4; e = e + 1) begin
      kv = {e_run[e], e_stop[e], e_step[e], e_exec[e], e_rst[e]};
      if ((kv & (kv - 5'd1)) != 5'd0) one_kind = 1'b0;
    end
  end

  always @(*) if (f_past) begin
    a_exec: assert ((e_exec & (e_runs | e_xm)) == 4'h0); // EXEC only to a stopped engine without a pending injection (D107)
    a_rsx:  assert (((e_run | e_step) & e_xm) == 4'h0); // RUN and STEP never to an engine with a pending injection (D107)
    a_one:  assert (one_kind); // at most one of RUN, STOP, STEP, EXEC, RST per engine per clock (D107)
  end
`ifndef LINK_CUT
  always @(*) if (f_past) begin
    h_pk:   assert (pk == 3'd0 || rx_st == 2'd0); // command pulse clock: receiver idle, so the next clock has no pulse
  end
`endif

  always @(*) if (f_past) begin
    c_exec:     cover (e_exec[2] && e_runs[1] && e_xm[3]);
    c_rsx:      cover (e_run == 4'b1011 && e_xm[2]);
    c_rsx_step: cover (e_step == 4'b0001 && e_xm[1]);
    c_one:      cover (e_stop == 4'b1111);
    c_pk:       cover (pk == 3'd5 && e_rst == 4'b0110);
  end
  // ---- B. framing: byte-level parser model of HOSTIF 2-3 (ESC 0x1B, op, fixed raw arguments, WRMEM words) ----
  function [1:0] nargs;
    input [7:0] o;
    nargs = (o >= 8'h60 && o <= 8'h63) ? 2'd2 : (o == 8'h70 || o == 8'h71 || o == 8'h72) ? 2'd3 : (o == 8'h73) ? 2'd1 : 2'd0;
  endfunction
  function known0;
    input [7:0] o;
    known0 = (o <= 8'h03) || (o >= 8'h20 && o <= 8'h5F) || o == 8'h74;
  endfunction

  reg [1:0] m_st, m_n, m_sel;
  reg [7:0] m_op, m_a0, m_a1, m_lo;
  reg [8:0] m_words, m_addr;
  reg       m_hi, m_selv;
  wire md_data = b_vld && ((m_st == 2'd0 && b != 8'h1B) || (m_st == 2'd1 && b == 8'h1B));
  wire md_cmd0 = b_vld && m_st == 2'd1 && b != 8'h1B && known0(b);
  wire md_last = b_vld && m_st == 2'd2 && ({1'b0, m_n} + 3'd1 == {1'b0, nargs(m_op)});
  wire md_word = b_vld && m_st == 2'd3 && m_hi;
  wire wrreg   = md_last && m_op == 8'h72;
  always @(posedge clk) begin
    if (!rst_n) begin
      m_st   <= 2'd0;
      m_selv <= 1'b0;
      m_sel  <= 2'd0;
    end else begin
      if (rx_brk) m_st <= 2'd0;
      else if (b_vld)
        case (m_st)
          2'd0: if (b == 8'h1B) m_st <= 2'd1;
          2'd1: if (b != 8'h1B && nargs(b) != 2'd0) begin
                  m_st <= 2'd2;
                  m_op <= b;
                  m_n  <= 2'd0;
                end else m_st <= 2'd0;
          2'd2: begin
                  if (m_n == 2'd0) m_a0 <= b;
                  if (m_n == 2'd1) m_a1 <= b;
                  if (md_last) begin
                    if (m_op == 8'h70) begin
                      m_st    <= 2'd3;
                      m_words <= {1'b0, b} + 9'd1;
                      m_addr  <= {m_a1[0], m_a0};
                      m_hi    <= 1'b0;
                    end else m_st <= 2'd0;
                  end else m_n <= m_n + 2'd1;
                end
          default: if (!m_hi) begin
                  m_lo <= b;
                  m_hi <= 1'b1;
                end else begin
                  m_hi    <= 1'b0;
                  m_addr  <= m_addr + 9'd1;
                  m_words <= m_words - 9'd1;
                  if (m_words == 9'd1) m_st <= 2'd0;
                end
        endcase
      if (md_cmd0 && b <= 8'h03) begin
        m_selv <= 1'b1;
        m_sel  <= b[1:0];
      end
    end
  end

  // expected effects one clock after the last byte (HOSTIF 3, 5, 9)
  wire [3:0] xm_e = 4'b0001 << m_op[1:0];
  reg  [3:0] x_stop, x_run, x_step, x_rst, x_exec;
  reg  [15:0] x_word, x_filt;
  reg  [7:0] x_sm;
  reg        brk_q;
  always @(posedge clk) begin
    x_stop <= (rst_n && md_cmd0 && b[7:4] == 4'h2) ? b[3:0] : 4'h0;
    x_run  <= (rst_n && md_cmd0 && b[7:4] == 4'h3) ? b[3:0] & ~e_xm : 4'h0;
    x_step <= (rst_n && md_cmd0 && b[7:4] == 4'h4) ? b[3:0] & ~e_xm : 4'h0;
    x_rst  <= (rst_n && md_cmd0 && b[7:4] == 4'h5) ? b[3:0] : 4'h0;
    x_exec <= (rst_n && md_last && nargs(m_op) == 2'd2 && (xm_e & (e_runs | e_xm)) == 4'h0) ? xm_e : 4'h0;
    x_word <= {b, m_a0};
    x_filt <= !rst_n ? 16'h0000 : (wrreg && m_a0 == 8'h04) ? {b, m_a1} : filt;
    x_sm   <= !rst_n ? 8'hE4 : (wrreg && m_a0 == 8'h02 && (e_runs | e_xm) == 4'h0) ? m_a1 : slotmap;
    brk_q  <= rst_n && rx_brk;
  end
  wire [3:0] full = {rx_free[11:9] == 3'd0, rx_free[8:6] == 3'd0, rx_free[5:3] == 3'd0, rx_free[2:0] == 3'd0};
  wire       padw = wrreg && m_a0[7:4] == 4'h1 && m_a0 != 8'h14 && m_a0 != 8'h15;

  always @(*) if (f_past) begin
    a_data:  assert (rx_wr == ((md_data && m_selv) ? 4'b0001 << m_sel : 4'h0) && (rx_wr == 4'h0 || rx_wdata == b) && rx_ovf == (rx_wr & full)); // data byte (incl. ESC ESC) to the selected stream only, rx_ovf = toward a full FIFO
    a_cmd:   assert (e_stop == x_stop && e_run == x_run && e_step == x_step && e_rst == x_rst && e_exec == x_exec); // engine pulses = the command 1 clk after its last byte, with the HOSTIF 9 refusals
    a_xword: assert (e_exec == 4'h0 || exec_word == x_word); // EXEC word = {hi, lo} of its arguments
    a_reg:   assert (pad_we == padw && (!padw || (pad_idx == m_a0[3:0] && pad_wdata == m_a1[6:0])) && sig_we == (wrreg && m_a0 == 8'h03) && (!sig_we || sig_wdata == m_a1) && e_clr == ((wrreg && m_a0 == 8'h06) ? m_a1 : 8'h00) && filt == x_filt && slotmap == x_sm); // WRREG effects (HOSTIF 5); SLOTMAP only with no engine running or with xm
    a_brk:   assert (!brk_q || p_st == 2'd0); // a break returns the parser to the data state
    h_pst:   assert (p_st == m_st && sel_v == m_selv && (!m_selv || sel == m_sel) && (m_st != 2'd2 || (op == m_op && argi == m_n && need == nargs(m_op) && m_n < nargs(m_op) && (m_n == 2'd0 || a0 == m_a0) && (m_n < 2'd2 || a1 == m_a1))) && (m_st != 2'd3 || (wr_left == m_words && m_words != 9'd0 && wr_lo_v == m_hi && wr_addr == m_addr && (!m_hi || a0 == m_lo)))); // parser state = model state (legal; args, words, selection)
  end

  always @(*) if (f_past) begin
    c_data:       cover (rx_wr[2] && rx_wdata == 8'h1B);
    c_cmd:        cover (e_rst == 4'b1001);
    c_xword:      cover (e_exec[1] && exec_word == 16'hABCD);
    c_cmd_refuse: cover (md_last && m_op == 8'h60 && e_runs[0]);
    c_reg:        cover (pad_we && pad_idx == 4'hB && pad_wdata == 7'h45);
    c_reg_sm:     cover (slotmap != 8'hE4);
    c_brk:        cover (brk_q && m_selv && m_sel == 2'd3);
    c_pst:        cover (m_st == 2'd3 && m_words == 9'd2 && m_hi);
  end

  // ---- C. H_RDY (D080), chip -> host escapes (HOSTIF 4, D085), memory requests ----
  reg  [11:0] free_q;
  always @(posedge clk) free_q <= rx_free;
  wire [2:0] fsel = free_q[3*m_sel +: 3];

  wire       rp_new_m = (md_last && (m_op == 8'h71 || m_op == 8'h73)) || (md_cmd0 && b == 8'h74);
  wire [7:0] rp_cmd   = (md_last && m_op == 8'h71) ? 8'hC1 : (md_last && m_op == 8'h73) ? 8'hC3 : 8'hC4;
  wire [8:0] ent_m    = tx_rdata[9*cur +: 9];
  wire       st_b2    = tx_ld && (take_nt || sw_st || (tx_rd != 4'h0 && (ent_m[8] || ent_m[7:0] == 8'h1B)));
  wire       st_rp    = tx_ld && take_rp && (rp_new_m || rp_hdr == 2'd2);
  wire       code_ok  = b2_n == 8'h1B || b2_n <= 8'h03 || (b2_n >= 8'h40 && b2_n <= 8'h8C);

  // an escape's code byte is owed as the next byte loaded; a new reply drops an owed reply code (D085)
  reg       x_v, m_curv;
  reg [7:0] x_b;
  reg [1:0] x_kind, m_cur;
  always @(posedge clk) begin
    if (!rst_n) begin
      x_v    <= 1'b0;
      m_curv <= 1'b0;
    end else if (tx_ld) begin
      x_v    <= st_b2 || st_rp;
      x_b    <= st_b2 ? b2_n : rp_new_m ? rp_cmd : rp_code;
      x_kind <= sw_st ? 2'd1 : st_b2 ? 2'd2 : 2'd3;
      if (x_v && x_kind == 2'd1) begin
        m_cur  <= tx_byte[1:0];
        m_curv <= 1'b1;
      end
    end else if (rp_new_m && x_kind == 2'd3) x_v <= 1'b0;
  end

  // pending memory write (one word), read request hold
  reg        m_pend, rq_q;
  reg [8:0]  m_paddr, ra_q;
  reg [15:0] m_pdata;
  always @(posedge clk) begin
    if (!rst_n) m_pend <= 1'b0;
    else if (md_word) begin
      m_pend  <= 1'b1;
      m_paddr <= m_addr;
      m_pdata <= {b, m_lo};
    end else if (h_gnt && h_we) m_pend <= 1'b0;
    rq_q <= rst_n && h_req && !h_we && !h_gnt && !rp_new_m;
    ra_q <= h_addr;
  end

  always @(*) if (f_past) begin
    a_rdy:    assert (h_rdy == ((m_st == 2'd0 || m_st == 2'd1) && m_selv && fsel < 3'd2)); // H_RDY high <=> data state or after ESC, stream selected, its RX FIFO < 2 free (D080), registered
    a_esc:    assert (!(x_v && tx_ld) || tx_byte == x_b || (x_kind == 2'd3 && rp_new_m)); // the code byte of an escape is the next byte sent after its ESC (never interleaved; reply code: unless a new reply replaces it, D085)
    a_code:   assert (!st_b2 || (tx_byte == 8'h1B && code_ok)); // notice, stream change, event and literal escapes start with ESC and carry a valid code
    a_enc:    assert (tx_rd == 4'h0 || (tx_rd == 4'b0001 << cur && tx_ld && (ent_m[8] ? (tx_byte == 8'h1B && b2_n == {2'b01, ent_m[5:0]}) : (ent_m[7:0] == 8'h1B) ? (tx_byte == 8'h1B && b2_n == 8'h1B) : tx_byte == ent_m[7:0]))); // stream entry: event -> ESC 0x40|c, data 0x1B -> ESC 0x1B, other data raw
    a_cur:    assert (tx_rd == 4'h0 || (m_curv && tx_rd == 4'b0001 << m_cur && !tx_empty[m_cur])); // stream data only from the stream last announced by ESC e, and only when it has data
    h_strm:   assert ((x_v && x_kind == 2'd1) ? (cur_v && cur == x_b[1:0] && x_b[7:2] == 6'd0) : (cur_v == m_curv && (!m_curv || cur == m_cur))); // current stream = last announced one (or the one being announced)
    h_b2:     assert ((x_v && x_kind != 2'd3) == b2v && (!b2v || b2 == x_b)); // notice/stream escape: its code waits in b2
    h_rp:     assert ((x_v && x_kind == 2'd3) == (rp_act && rp_hdr == 2'd1) && (!(x_v && x_kind == 2'd3) || rp_code == x_b)); // reply escape: its code is owed by the reply
    a_mem:    assert (h_we == m_pend && (!m_pend || (h_req && h_addr == m_paddr && h_wdata == m_pdata))); // a WRMEM word is requested until granted, address and data held (replaced only by the next word: lost write)
    a_rdhold: assert (!rq_q || (h_req && (h_we || h_addr == ra_q))); // a read request not granted stays with its address (a pending write may take the port first)
  end

  always @(*) if (f_past) begin
    c_rdy:      cover (h_rdy && m_sel == 2'd1 && m_st == 2'd0);
    c_rdy_esc:  cover (h_rdy && m_st == 2'd1);
    c_esc:      cover (tx_ld && x_v && x_kind == 2'd1);
    c_esc_rp:   cover (tx_ld && x_v && x_kind == 2'd3 && tx_byte == 8'hC4);
    c_esc_drop: cover (x_v && x_kind == 2'd3 && rp_new_m && !tx_ld);
    c_code:     cover (st_b2 && take_nt && b2_n == 8'h8C);
    c_enc:      cover (tx_rd[2] && ent_m[8]);
    c_enc_lit:  cover (tx_rd[1] && ent_m == 9'h01B);
    c_cur:      cover (tx_rd[3] && m_cur == 2'd3);
    c_strm:     cover (x_v && x_kind == 2'd1 && cur == 2'd2);
    c_b2:       cover (b2v && x_kind == 2'd2 && b2 == 8'h8C);
    c_rp:       cover (rp_act && rp_hdr == 2'd1 && rp_code == 8'hC1);
    c_mem:      cover (h_we && h_gnt);
    c_mem_lost: cover (md_word && m_pend && !(h_gnt && h_we));
    c_rdhold:   cover (rq_q && h_req && !h_we);
  end
endmodule

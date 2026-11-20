`default_nettype none
// Properties of jspe_sig (VERIF_PLAN 6, ARCH 2.6, D048, D073)
module fv_sig (
    input wire        clk,
    input wire        rst_n,
    input wire [31:0] e_set,
    input wire [31:0] e_clr,
    input wire        sig_we,
    input wire [7:0]  sig_wdata
);
  wire [7:0] sig;

  jspe_sig dut (
      .clk      (clk),
      .rst_n    (rst_n),
      .e_set    (e_set),
      .e_clr    (e_clr),
      .sig_we   (sig_we),
      .sig_wdata(sig_wdata),
      .sig      (sig)
  );

  // reset in the first clock; checks start after it
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (!rst_n);

  // ARCH 2.6: S, C = OR over the engines (engine i at [8i +: 8])
  reg [7:0] sx, cx;
  integer i;
  always @(*) begin
    sx = 8'h00;
    cx = 8'h00;
    for (i = 0; i < 4; i = i + 1) begin
      sx = sx | e_set[8*i +: 8];
      cx = cx | e_clr[8*i +: 8];
    end
  end

  reg [7:0] x_g, g_q, s_q, c_q, wd_q;
  reg       we_q, run_q, sc_q;
  always @(posedge clk) begin
    x_g   <= !rst_n ? 8'h00 : sig_we ? sig_wdata : (sig | sx) & ~cx;
    g_q   <= sig;
    s_q   <= sx;
    c_q   <= cx;
    wd_q  <= sig_wdata;
    we_q  <= sig_we;
    run_q <= rst_n;
    sc_q  <= e_set[2] && e_clr[26];
  end

  always @(*) if (f_past) begin
    a_upd:  assert (sig == x_g); // G' = (G | S) & ~C, S and C ORed over the engines; a host write replaces the result; reset 0 (ARCH 2.6)
    a_clr:  assert (!run_q || we_q || (sig & c_q) == 8'h00); // clear wins over set, from any engine
    a_host: assert (!run_q || !we_q || sig == wd_q); // a host write replaces the engine result of its clock
    a_set:  assert (!run_q || we_q || (s_q & ~c_q & ~sig) == 8'h00); // a set without a clear makes the flag 1
    a_hold: assert (!run_q || we_q || ((sig ^ g_q) & ~(s_q | c_q)) == 8'h00); // a flag changes only by a set, a clear, a host write or reset
  end

  always @(*) if (f_past) begin
    c_upd:  cover (sig == 8'hA5 && !we_q);
    c_clr:  cover (run_q && !we_q && sc_q && !sig[2]);
    c_host: cover (run_q && we_q && (s_q | c_q) != 8'h00 && sig != ((g_q | s_q) & ~c_q));
    c_set:  cover (run_q && !we_q && s_q[7] && !c_q[7] && sig[7] && !g_q[7]);
    c_hold: cover (run_q && !we_q && s_q == 8'h00 && c_q == 8'h00 && sig == 8'h3C);
  end
endmodule

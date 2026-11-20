  // largest cyclic distance between consecutive slots of engine e (1..4); 0 = owns no slot
  function [2:0] gap;
    input [7:0] map;
    input [1:0] e;
    integer s, d;
    reg [2:0] dn;
    begin
      gap = 3'd0;
      for (s = 0; s < 4; s = s + 1)
        if (map[2*s +: 2] == e) begin
          dn = 3'd4;
          for (d = 3; d >= 1; d = d - 1)
            if (map[2*((s + d) % 4) +: 2] == e) dn = d;
          if (dn > gap) gap = dn;
        end
    end
  endfunction
  // ISA 10 K (chained access step): max over own slots s of (first own slot >= s + 2) - s; 0 = owns no slot
  function [2:0] kgap;
    input [7:0] map;
    input [1:0] e;
    integer s, d;
    reg [2:0] dn;
    begin
      kgap = 3'd0;
      for (s = 0; s < 4; s = s + 1)
        if (map[2*s +: 2] == e) begin
          dn = 3'd4;
          for (d = 3; d >= 2; d = d - 1)
            if (map[2*((s + d) % 4) +: 2] == e) dn = d;
          if (dn > kgap) kgap = dn;
        end
    end
  endfunction

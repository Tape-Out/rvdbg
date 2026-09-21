package Dtm;

// JTAG DTM（Debug Spec 1.0 6.1）。TAP 跑在系统时钟里：TCK、TMS、TDI 各过两级同步，按 TCK 的沿推进，没有跨时钟域。
// 代价是 TCK 周期至少 8 个系统时钟拍：管脚变了之后同步两拍、找沿并更新 TDO 一拍，TDO 第 3 拍才有效，主机在上升沿前的最后一拍采，低电平至少 4 拍。
// DMI 在 Update-DR 那一拍直接访问调试模块：调试模块零等待、从不答错，dmi 的 op 读回恒 0，dtmcs 的 idle 取 0

import RegIf::*;
import Tap::*;

interface Jtag;
  (* always_ready, always_enabled, prefix = "" *)
  method Action pins((* port = "tck" *) Bit#(1) tck, (* port = "tms" *) Bit#(1) tms, (* port = "tdi" *) Bit#(1) tdi);
  (* always_ready, result = "tdo" *)
  method Bit#(1) tdo;
endinterface

module mkDtm#(RegIf#(7, 32) dmi, Bit#(32) idcode)(Jtag);
  Reg#(Bit#(3))  s1   <- mkReg(0);
  Reg#(Bit#(3))  s2   <- mkReg(0);
  Reg#(Bit#(1))  last <- mkReg(0);
  Reg#(Tap)      st   <- mkReg(Tlr);
  Reg#(Bit#(5))  ir   <- mkReg(5'h01);
  Reg#(Bit#(5))  irSh <- mkReg(0);
  Reg#(Bit#(41)) dr   <- mkReg(0);
  Reg#(Bit#(1))  tdoR <- mkReg(0);
  Reg#(Bit#(7))  addr <- mkReg(0);
  Reg#(Bit#(32)) data <- mkReg(0);

  Wire#(Bit#(3)) pad <- mkBypassWire;

  Bit#(1) ck = s2[2];
  Bool    ms = s2[1] == 1;
  Bit#(1) di = s2[0];

  Bit#(32) dtmcs = {11'd0, 3'd0, 2'd0, 1'b0, 3'd0, 2'd0, 6'd7, 4'd1};

  // dmi 是 abits 7 加 34 位，IDCODE 与 dtmcs 32 位，没实现的指令都是 BYPASS 的 1 位
  Bit#(6) len = ir == 5'h11 ? 41 : ((ir == 5'h01 || ir == 5'h10) ? 32 : 1);
  Bit#(41) captured = case (ir)
    5'h01:   zeroExtend(idcode);
    5'h10:   zeroExtend(dtmcs);
    5'h11:   {addr, data, 2'b00};
    default: 0;
  endcase;

  rule tap;
    s1   <= pad;
    s2   <= s1;
    last <= ck;
    if (ck == 1 && last == 0) begin
      case (st)
        Tlr:   ir   <= 5'h01;
        CapIr: irSh <= 5'b00001;
        ShIr:  irSh <= {di, irSh[4:1]};
        UpdIr: ir   <= irSh;
        CapDr: dr   <= captured;
        ShDr:  dr   <= (dr >> 1) | (zeroExtend(di) << (len - 1));
        UpdDr:
          if (ir == 5'h10) begin
            if (dr[17] == 1) begin
              addr <= 0;
              data <= 0;
            end
          end else if (ir == 5'h11 && (dr[1:0] == 1 || dr[1:0] == 2)) begin
            let r <- dmi.access(RegReq { addr: dr[40:34], write: dr[1:0] == 2, wdata: dr[33:2], wstrb: 4'hF });
            addr <= dr[40:34];
            data <= dr[1:0] == 1 ? r.rdata : dr[33:2];
          end
      endcase
      st <= advance(st, ms);
    end else if (ck == 0 && last == 1)
      tdoR <= st == ShIr ? irSh[0] : (st == ShDr ? dr[0] : 0);
  endrule

  method Action pins(Bit#(1) tck, Bit#(1) tms, Bit#(1) tdi);
    pad <= {tck, tms, tdi};
  endmethod

  method Bit#(1) tdo = tdoR;
endmodule

endpackage

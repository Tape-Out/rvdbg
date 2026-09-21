package Dm;

// RISC-V 调试模块（Debug Spec 1.0 第 3 章）：一颗核、不带程序缓冲，抽象命令直接交给核做，不走调试 ROM。
// dmi 是 DMI 那一侧（地址 7 位，零等待）；core 给核：haltreq、resumereq 两根电平、halted 一根回线，
// 寄存器与内存访问各一个 RegManager，接核时照 mkPipe 连到核的 RegTarget。
// 状态只在 step 一条规则里写，DMI 的写与核的答复都走线，同拍的次序只写在一处：
// busy 期间碰过 command、abstractcs、data 记 1 → 在途那一笔的答复 → data、command 的写 → dmcontrol（dmactive 写 0 盖过一切）

import RegIf::*;
import DmCmd::*;

interface DmCore;
  interface RegManager#(16, 32) regs;
  interface RegManager#(32, 32) mem;
  (* always_ready *) method Bool haltreq;
  (* always_ready *) method Bool resumereq;
  (* always_ready *) method Bool ndmreset;
  (* always_ready, always_enabled *) method Action status(Bool halted);
endinterface

interface DmIfc;
  interface RegIf#(7, 32) dmi;
  interface DmCore        core;
endinterface

module mkDm(DmIfc);
  Reg#(Bool)     active  <- mkReg(False);
  Reg#(Bool)     ndm     <- mkReg(False);
  Reg#(Bool)     hreq    <- mkReg(False);
  Reg#(Bool)     rpend   <- mkReg(False);
  Reg#(Bool)     rack    <- mkReg(False);
  Reg#(Bool)     haveRst <- mkReg(True);
  Reg#(Bool)     busy    <- mkReg(False);
  Reg#(Cmd)      cur     <- mkReg(tagged Nop);
  Reg#(Bit#(3))  cmderr  <- mkReg(0);
  Reg#(Bit#(32)) data0   <- mkReg(0);
  Reg#(Bit#(32)) data1   <- mkReg(0);

  Wire#(Bool)         halted <- mkBypassWire;
  RWire#(Bit#(32))    ctlW   <- mkRWire;
  RWire#(Bit#(32))    d0W    <- mkRWire;
  RWire#(Bit#(32))    d1W    <- mkRWire;
  RWire#(Bit#(32))    csW    <- mkRWire;
  RWire#(Bit#(32))    cmdW   <- mkRWire;
  PulseWire           clash  <- mkPulseWire;
  RWire#(RegRsp#(32)) regR   <- mkRWire;
  RWire#(RegRsp#(32)) memR   <- mkRWire;

  rule step;
    Bool     act = active;
    Bool     nd  = ndm;
    Bool     hq  = hreq;
    Bool     rp  = rpend;
    Bool     ra  = rack;
    Bool     hr  = haveRst;
    Bool     bz  = busy;
    Cmd      c   = cur;
    Bit#(3)  ce  = cmderr;
    Bit#(32) d0  = data0;
    Bit#(32) d1  = data1;

    // 3.7：busy 期间记下的 1 在前，命令自己的错只在 cmderr 还是 0 时才记
    if (clash && ce == 0) ce = 1;

    Maybe#(RegRsp#(32)) got = onMem(cur) ? memR.wget : regR.wget;
    if (busy &&& got matches tagged Valid .x) begin
      bz = False;
      if (x.err) begin
        if (ce == 0) ce = onMem(cur) ? 5 : 3;
      end else begin
        if (!isWrite(cur)) d0 = x.rdata;
        if (postinc(cur)) d1 = d1 + 4;
      end
    end

    if (rp && !halted) begin
      rp = False;
      ra = True;
    end

    // busy 期间 data 与 command 的写不收：在途那一笔的请求要顶着 data0、data1 不动
    if (active && !busy) begin
      if (d0W.wget matches tagged Valid .w) d0 = w;
      if (d1W.wget matches tagged Valid .w) d1 = w;
      if (csW.wget matches tagged Valid .w) ce = ce & ~w[10:8];
      if (cmdW.wget matches tagged Valid .w &&& ce == 0) begin
        Cmd k = decode(w);
        if (k == tagged Unsupported)
          ce = 2;
        else if (k != tagged Nop) begin
          if (!halted) ce = 4;
          else begin
            c  = k;
            bz = True;
          end
        end
      end
    end

    if (ctlW.wget matches tagged Valid .w) begin
      if (w[0] == 0) begin
        // 在途那一笔一并丢下，核后来的答复不收（3.7：命令挂住时调试器靠 dmactive 解）
        act = False; nd = False; hq = False; rp = False; ra = False;
        bz  = False; c  = tagged Nop; ce = 0; d0 = 0; d1 = 0;
      end else begin
        act = True;
        nd  = w[1] == 1;
        if (w[1] == 1) hr = True;
        if (!busy) begin
          hq = w[31] == 1;
          // 核在跑时没有要等的恢复过程，ack 当拍就算完成
          if (w[30] == 1 && w[31] == 0) begin
            rp = halted;
            ra = !halted;
          end
          if (w[28] == 1) hr = False;
        end
      end
    end

    active  <= act;
    ndm     <= nd;
    hreq    <= hq;
    rpend   <= rp;
    rack    <= ra;
    haveRst <= hr;
    busy    <= bz;
    cur     <= c;
    cmderr  <= ce;
    data0   <= d0;
    data1   <= d1;
  endrule

  interface RegIf dmi;
    method ActionValue#(RegRsp#(32)) access(RegReq#(7, 32) r);
      Dm s = Dm { active: active, ndmreset: ndm, halted: halted, resumeack: rack,
                  havereset: haveRst, busy: busy, cmderr: cmderr, data0: data0, data1: data1 };
      Bool dat = r.addr == 7'h04 || r.addr == 7'h05;
      if (busy && (dat || (r.write && (r.addr == 7'h16 || r.addr == 7'h17)))) clash.send;
      if (r.write)
        case (r.addr)
          7'h04: d0W.wset(r.wdata);
          7'h05: d1W.wset(r.wdata);
          7'h10: ctlW.wset(r.wdata);
          7'h16: csW.wset(r.wdata);
          7'h17: cmdW.wset(r.wdata);
        endcase
      return RegRsp { rdata: r.write ? 0 : readDm(s, r.addr), err: False };
    endmethod
  endinterface

  interface DmCore core;
    interface RegManager regs;
      method Bool valid = busy && !onMem(cur);
      method RegReq#(16, 32) req = RegReq { addr: regOf(cur), write: isWrite(cur), wdata: data0, wstrb: 4'hF };
      method Action ready(Bool v);
      endmethod
      method Action resp(Bool v, RegRsp#(32) x);
        if (v) regR.wset(x);
      endmethod
    endinterface
    interface RegManager mem;
      method Bool valid = busy && onMem(cur);
      method RegReq#(32, 32) req = RegReq { addr: data1, write: isWrite(cur), wdata: data0, wstrb: 4'hF };
      method Action ready(Bool v);
      endmethod
      method Action resp(Bool v, RegRsp#(32) x);
        if (v) memR.wset(x);
      endmethod
    endinterface
    method Bool haltreq   = hreq;
    method Bool resumereq = rpend;
    method Bool ndmreset  = ndm;
    method Action status(Bool h);
      halted <= h;
    endmethod
  endinterface
endmodule

endpackage

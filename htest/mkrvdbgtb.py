"""rvdbg 的行为测试台：只从 JTAG 进，经 DMI 走调试模块的全部判据；核是行为模型。判据见 notes/规范对照/rvdbg.md。

一，DTM：复位后 IR 为 IDCODE（按参数算）；Capture-IR 低两位 01；没实现的指令选 BYPASS；dtmcs 读 0x71。
二，调试模块激活前只收 dmcontrol 的写；激活后的只读字段；hartsel 是 WARL 0。
三，核在跑时要访问的命令报 4；transfer 为 0 不看 aarsize。
四，haltreq 停核，读 dcsr、dpc。
五，Access Register 写读 x1，核没有的寄存器报 3。
六，Access Memory 写读与自增，没有存储的地址报 5。
七，不支持的选项报 2，cmderr 非零时新命令不起。
八，busy 期间碰 data0 记 1 且写不收、resumereq 不收，在途命令用起步时的 data0。
九，resumereq；ndmreset；dmactive 写 0 的复位。
十，dmi 的 nop 不留痕，dtmhardreset 清 dmi，TMS 复位回 IDCODE，TCK 换成 16 拍周期再读一遍。

扫描不写成 StmtFSM：一步一个语句，两百来步展开就撞 bsc 的 100 万步上限，改成生成一张指令表，由一条规则解释执行。
TCK 周期默认 8 拍，是 DTM 的下限。核模型的寄存器访问 2000 拍才答，经 JTAG 发出的后几次扫描才落得进 busy 窗口。
"""
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)
cfg = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
label = cfg.get("label", "")
knobs = cfg.get("knobs", {})
bank = int(knobs.get("bank", 0))
maker = int(knobs.get("maker", 0))
part = int(knobs.get("part", 0))
rev = int(knobs.get("rev", 0))
# IEEE 1149.1 的 IDCODE：修订号 31:28，零件号 27:12，续码个数模 16 放 11:8，厂商号低 7 位放 7:1，第 0 位恒 1
IDCODE = (rev << 28) | (part << 12) | ((bank % 16) << 8) | (maker << 1) | 1

prog = []


def op(kind, n=0, a=0, b=0, off=0, w=0, want=0, mask=None, what=""):
    m = ((1 << w) - 1) if mask is None else mask
    prog.append((kind, n, a, b, off, w, m, want & m, what))


def reset():
    op("OpReset")


def ir(v, **k):
    op("OpIr", a=v, **k)


def dr(n, v, **k):
    op("OpDr", n=n, a=v, **k)


def dmi(addr, data, opc):
    return (addr << 34) | (data << 2) | opc


def dw(addr, data):
    dr(41, dmi(addr, data, 2))


# 读一次 DMI：第一次扫描发读，Update-DR 之后不经 Run-Test/Idle 接一次 nop 扫描，把结果捕获出来
def rd(addr, want, what, mask=0xFFFF_FFFF):
    op("OpPair", a=dmi(addr, 0, 1), off=50, w=32, mask=mask, want=want, what=what)


def poll(addr, mask, want, what):
    op("OpPoll", a=dmi(addr, 0, 1), off=50, w=32, mask=mask, want=want, what=what)


def idle():
    poll(0x16, 0x1000, 0, "abstractcs busy clears")


def cmderr(want, what):
    rd(0x16, want << 8, what, mask=0x700)


def clear():
    dw(0x16, 0x700)


def pin(which, want, what):
    op("OpPin", a=which, w=1, want=want, what=what)


def half(h):
    op("OpHalf", a=h)


# 一，DTM
reset()
dr(32, 0, off=3, w=32, want=IDCODE, what="IDCODE is selected after reset")
ir(0x05, off=4, w=2, want=1, what="Capture-IR loads 01 into the two low bits")
dr(8, 0b0100_1101, off=3, w=8, want=0b1001_1010, what="instruction 0x05 is unimplemented and selects the one-bit BYPASS")
ir(0x1F)
dr(8, 0b0100_1101, off=3, w=8, want=0b1001_1010, what="instruction 0x1f selects BYPASS")
ir(0x10)
dr(32, 0, off=3, w=32, want=0x71, what="dtmcs: version 1, abits 7, idle 0, no errors")
ir(0x11)

# 二，激活与只读字段
dw(0x04, 0x5555_5555)
dw(0x10, 1)
rd(0x10, 1, "dmactive reads back 1")
rd(0x04, 0, "a data0 write before dmactive is dropped")
rd(0x11, 0x000C_0C83, "dmstatus: version 3, authenticated, running, havereset from power-up")
dw(0x10, 0x1000_0001)
rd(0x11, 0x0000_0C83, "ackhavereset clears havereset")
dw(0x10, 0x03FF_FFC1)
rd(0x10, 1, "hartsel is WARL 0 with a single hart")
rd(0x16, 2, "abstractcs: datacount 2, no program buffer")
rd(0x12, 0, "hartinfo reads zero")
rd(0x40, 0, "haltsum0 reads zero with a single hart")
rd(0x38, 0, "sbcs reads zero without system bus access")

# 三，核在跑
dw(0x17, 0x0022_1001)
idle()
cmderr(4, "reading x1 while the hart runs fails with cmderr 4")
clear()
cmderr(0, "cmderr is write-1-to-clear")
dw(0x17, 0x0030_1001)
idle()
cmderr(0, "transfer 0 does nothing and ignores aarsize")

# 四，停核
dw(0x10, 0x8000_0001)
poll(0x11, 0x200, 0x200, "the hart reports halted")
rd(0x11, 0x0000_0383, "allhalted and anyhalted after haltreq")
dw(0x10, 1)
dw(0x17, 0x0022_07B0)
idle()
cmderr(0, "reading dcsr reports no error")
rd(0x04, 0x4000_00C3, "dcsr from the hart lands in data0")
dw(0x17, 0x0022_07B1)
idle()
rd(0x04, 0x0000_0100, "dpc is where the hart stopped")

# 五，Access Register
dw(0x04, 0x1234_5678)
dw(0x17, 0x0023_1001)
idle()
dw(0x04, 0)
dw(0x17, 0x0022_1001)
idle()
cmderr(0, "writing and reading x1 report no error")
rd(0x04, 0x1234_5678, "x1 reads back what was written")
dw(0x17, 0x0022_1020)
idle()
cmderr(3, "a register the hart does not have fails with cmderr 3")
clear()

# 六，Access Memory
dw(0x04, 0xCAFE_BABE)
dw(0x05, 0x8000_0004)
dw(0x17, 0x0229_0000)
idle()
rd(0x05, 0x8000_0008, "aampostincrement adds 4 to data1")
dw(0x05, 0x8000_0004)
dw(0x04, 0)
dw(0x17, 0x0220_0000)
idle()
rd(0x04, 0xCAFE_BABE, "the word reads back from memory")
rd(0x05, 0x8000_0004, "data1 stays without aampostincrement")
cmderr(0, "writing and reading memory report no error")
dw(0x05, 0x0000_0010)
dw(0x17, 0x0220_0000)
idle()
cmderr(5, "an address without memory fails with cmderr 5")
clear()

# 七，不支持的选项
for cmd, what in [(0x0032_1001, "aarsize 3 on a 32-bit register fails with cmderr 2"),
                  (0x0026_1001, "postexec without a program buffer fails with cmderr 2"),
                  (0x002A_1001, "aarpostincrement is not supported"),
                  (0x0200_0000, "an 8-bit memory access is not supported"),
                  (0x02A0_0000, "aamvirtual is not supported")]:
    dw(0x17, cmd)
    idle()
    cmderr(2, what)
    clear()
dw(0x17, 0x0100_0000)
idle()
cmderr(2, "Quick Access fails with cmderr 2")
dw(0x04, 0x0000_0055)
dw(0x17, 0x0023_1001)
idle()
clear()
dw(0x17, 0x0022_1001)
idle()
rd(0x04, 0x1234_5678, "a command written while cmderr is set does not run")

# 八，busy 窗口
dw(0x04, 0xAAAA_0001)
dw(0x17, 0x0023_1001)
rd(0x16, 0x1000, "busy is set right after command is written", mask=0x1000)
dw(0x04, 0xBBBB_0002)
dw(0x10, 0x4000_0001)
idle()
cmderr(1, "touching data0 while busy leaves cmderr 1")
rd(0x11, 0x200, "resumereq written while busy is ignored", mask=0x200)
clear()
dw(0x04, 0)
dw(0x17, 0x0022_1001)
idle()
rd(0x04, 0xAAAA_0001, "the busy command used the data0 it started with")

# 九，恢复、ndmreset、dmactive 写 0
dw(0x10, 0x4000_0001)
poll(0x11, 0x2_0000, 0x2_0000, "the hart acknowledges the resume")
rd(0x11, 0x0003_0C83, "allresumeack and allrunning after resumereq")
dw(0x10, 0x0000_0003)
rd(0x10, 3, "ndmreset reads back")
pin(1, 1, "ndmreset drives its output")
rd(0x11, 0x010C_0000, "ndmreset sets ndmresetpending and havereset", mask=0x010C_0000)
dw(0x10, 0x1000_0001)
dw(0x10, 0x8000_0001)
poll(0x11, 0x200, 0x200, "the hart reports halted again")
dw(0x04, 0x0000_1111)
dw(0x17, 0x0100_0000)
idle()
dw(0x10, 0)
rd(0x16, 2, "dmactive 0 clears cmderr")
rd(0x04, 0, "dmactive 0 clears data0")
pin(0, 0, "dmactive 0 drops the halt request")
dw(0x04, 0x0000_2222)
dw(0x10, 1)
rd(0x04, 0, "a data0 write while inactive is dropped")

# 十，DTM 的其余几条
dw(0x04, 0xDEAD_BEEF)
rd(0x04, 0xDEAD_BEEF, "data0 written over dmi reads back over dmi")
dr(41, 0, off=5, w=32, want=0xDEAD_BEEF, what="a nop scan leaves the last result in dmi")
ir(0x10)
dr(32, 0x0002_0000)
ir(0x11)
dr(41, 0, off=3, w=41, want=0, what="dtmhardreset clears the address and data left in dmi")
reset()
dr(32, 0, off=3, w=32, want=IDCODE, what="a TMS reset selects IDCODE again")
half(8)
dr(32, 0, off=3, w=32, want=IDCODE, what="IDCODE reads with TCK at 16 system clock cycles")
ir(0x11)
rd(0x11, 0x0000_0383, "a dmi read of dmstatus works with TCK at 16 cycles")

assert len(prog) < 1024


def lit(v):
    return f"64'h{v:X}"


arms = []
fails = []
for i, (kind, n, a, b, off, w, m, want, what) in enumerate(prog):
    arms.append(f"    {i}: o = Op {{ kind: {kind}, len: {n}, a: {lit(a)}, b: {lit(b)}, "
                f"off: {off}, w: {w}, mask: {lit(m)}, want: {lit(want)} }};")
    if what:
        fails.append(f"      {i}: $display(\"FAIL {what}: got %h want %h\", got, want);")

verdict = ("over JTAG the DTM selects IDCODE, BYPASS, dtmcs and dmi as Debug Spec 6.1 requires, and the Debug Module "
           "activates, halts and resumes the hart, runs Access Register and Access Memory, rejects unsupported options, "
           "guards its busy window and resets as chapter 3 requires")

TEMPLATE = r'''package Rvdbg@L@Tb;

// 由 htest/mkrvdbgtb.py 生成，勿手改

import Vector::*;
import RegIf::*;
import Dm::*;
import Dtm::*;
import Rvdbg::*;

typedef enum { OpReset, OpIr, OpDr, OpPair, OpPoll, OpPin, OpHalf, OpEnd } OpKind deriving (Bits, Eq);

typedef struct {
  OpKind   kind;
  Bit#(8)  len;
  Bit#(64) a;
  Bit#(64) b;
  Bit#(8)  off;
  Bit#(8)  w;
  Bit#(64) mask;
  Bit#(64) want;
} Op deriving (Bits);

function Op progAt(Bit#(10) i);
  Op o = Op { kind: OpEnd, len: 0, a: 0, b: 0, off: 0, w: 0, mask: 0, want: 0 };
  case (i)
@PROG@
  endcase
  return o;
endfunction

(* synthesize *)
module mkRvdbg@L@Tb(Empty);
  RvdbgIfc#(12, 32, @PARAMS@) d <- mkRvdbg(RvdbgCfg { none: ? });

  // 核的行为模型：死循环停在 0x100；寄存器访问 2000 拍才答，内存访问第二拍答
  Reg#(Bool)                    halted <- mkReg(False);
  Reg#(Bit#(32))                dpc    <- mkReg(0);
  Reg#(Bit#(3))                 cause  <- mkReg(0);
  Reg#(Vector#(32, Bit#(32)))   gpr    <- mkReg(replicate(0));
  Reg#(Vector#(4, Bit#(32)))    ram    <- mkReg(replicate(0));
  Reg#(Bit#(12))                rgN    <- mkReg(0);
  Reg#(Maybe#(RegReq#(16, 32))) rgSeen <- mkReg(tagged Invalid);
  Reg#(Bool)                    rgV    <- mkReg(False);
  Reg#(RegRsp#(32))             rgR    <- mkReg(RegRsp { rdata: 0, err: False });
  Reg#(Bool)                    mmV    <- mkReg(False);
  Reg#(RegRsp#(32))             mmR    <- mkReg(RegRsp { rdata: 0, err: False });
  Reg#(Bool)                    badHw  <- mkReg(False);

  Wire#(Bool)            rqV <- mkBypassWire;
  Wire#(RegReq#(16, 32)) rq  <- mkBypassWire;
  Wire#(Bool)            mqV <- mkBypassWire;
  Wire#(RegReq#(32, 32)) mq  <- mkBypassWire;

  RegTarget#(16, 32) hartRegs = interface RegTarget;
    method Action req(Bool v, RegReq#(16, 32) r);
      rqV <= v;
      rq  <= r;
    endmethod
    method Bool        ready    = True;
    method Bool        rspValid = rgV;
    method RegRsp#(32) rsp      = rgR;
  endinterface;

  RegTarget#(32, 32) hartMem = interface RegTarget;
    method Action req(Bool v, RegReq#(32, 32) r);
      mqV <= v;
      mq  <= r;
    endmethod
    method Bool        ready    = True;
    method Bool        rspValid = mmV;
    method RegRsp#(32) rsp      = mmR;
  endinterface;

  Empty pipeRegs <- mkPipe(d.core.regs, hartRegs);
  Empty pipeMem  <- mkPipe(d.core.mem, hartMem);

  rule drive;
    d.core.status(halted);
  endrule

  rule hart;
    Bool                  h  = halted;
    Bit#(32)              nd = dpc;
    Bit#(3)               nc = cause;
    Vector#(32, Bit#(32)) g  = gpr;
    Vector#(4, Bit#(32))  m  = ram;
    Bool unstable = False;
    Bool running  = False;

    if (!halted && d.core.haltreq) begin
      h  = True;
      nd = 32'h100;
      nc = 3;
    end else if (halted && d.core.resumereq)
      h = False;

    if (rgV) begin
      rgV <= False;
    end else if (rqV) begin
      if (rgSeen matches tagged Valid .p &&& pack(p) != pack(rq)) unstable = True;
      if (!halted) running = True;
      if (rgN == 2000) begin
        RegRsp#(32) x = RegRsp { rdata: 0, err: False };
        Bit#(16) a = rq.addr;
        if (a >= 16'h1000 && a <= 16'h101F) begin
          Bit#(5) i = truncate(a);
          if (i != 0) begin
            x.rdata = g[i];
            if (rq.write) g[i] = rq.wdata;
          end
        end else if (a == 16'h07B0)
          x.rdata = {4'd4, 19'd0, cause, 4'd0, 2'b11};
        else if (a == 16'h07B1) begin
          x.rdata = dpc;
          if (rq.write) nd = rq.wdata;
        end else
          x.err = True;
        rgR    <= x;
        rgV    <= True;
        rgN    <= 0;
        rgSeen <= tagged Invalid;
      end else begin
        rgN    <= rgN + 1;
        rgSeen <= tagged Valid rq;
      end
    end

    if (mmV) begin
      mmV <= False;
    end else if (mqV) begin
      if (!halted) running = True;
      RegRsp#(32) y = RegRsp { rdata: 0, err: False };
      Bit#(32) a = mq.addr;
      if (a >= 32'h8000_0000 && a <= 32'h8000_000C && a[1:0] == 0) begin
        Bit#(2) i = truncate(a >> 2);
        y.rdata = m[i];
        if (mq.write) m[i] = mq.wdata;
      end else
        y.err = True;
      mmR <= y;
      mmV <= True;
    end

    halted <= h;
    dpc    <= nd;
    cause  <= nc;
    gpr    <= g;
    ram    <= m;
    if ((unstable || running) && !badHw) begin
      if (unstable) $display("FAIL the register request changed before its response");
      if (running)  $display("FAIL an access was sent to a running hart");
      badHw <= True;
    end
  endrule

  // JTAG 按位驱：一个 TCK 周期 2×half 拍，TDO 在管脚低电平的最后一拍（写 tck 为 1 的那一拍）采。
  // 移位用的寄存器与 half 只由 bang 写，解释规则只写线，两条规则于是排得出先后
  Reg#(Bit#(1))   tck   <- mkReg(0);
  Reg#(Bit#(1))   tms   <- mkReg(1);
  Reg#(Bit#(1))   tdi   <- mkReg(0);
  Reg#(Bit#(128)) tmsQ  <- mkReg(0);
  Reg#(Bit#(128)) tdiQ  <- mkReg(0);
  Reg#(Bit#(128)) tdoQ  <- mkReg(0);
  Reg#(Bit#(8))   n     <- mkReg(0);
  Reg#(Bit#(8))   k     <- mkReg(0);
  Reg#(Bit#(6))   ph    <- mkReg(0);
  Reg#(Bit#(6))   half  <- mkReg(4);
  Reg#(Bool)      going <- mkReg(False);

  RWire#(Tuple3#(Bit#(128), Bit#(128), Bit#(8))) startW <- mkRWire;
  RWire#(Bit#(6))                                halfW  <- mkRWire;

  rule pads;
    d.jtag.pins(tck, tms, tdi);
  endrule

  rule bang;
    if (halfW.wget matches tagged Valid .hv) half <= hv;
    if (startW.wget matches tagged Valid {.sm, .si, .sn}) begin
      tmsQ  <= sm;
      tdiQ  <= si;
      tdoQ  <= 0;
      n     <= sn;
      k     <= 0;
      ph    <= 0;
      going <= True;
    end else if (going) begin
      Bit#(1) c = tck;
      if (ph == 0) begin
        c = 0;
        tms <= tmsQ[k];
        tdi <= tdiQ[k];
      end else if (ph == half)
        c = 1;
      tck <= c;
      if (ph == half) tdoQ[k] <= d.jtag.tdo;
      if (ph == 2 * half - 1) begin
        ph <= 0;
        k  <= k + 1;
        if (k + 1 == n) going <= False;
      end else
        ph <= ph + 1;
    end
  endrule

  function Tuple3#(Bit#(128), Bit#(128), Bit#(8)) scanOf(Op o);
    case (o.kind)
      // TMS 连 6 个 1 再一个 0：回到 Test-Logic-Reset，停在 Run-Test/Idle
      OpReset: return tuple3(128'b011_1111, 0, 7);
      // IR：TMS 1 1 0 0，5 位（最后一位 TMS 1），再 1 0；TDO 在第 4 到 8 位
      OpIr:    return tuple3(128'b01_10000_0011, zeroExtend(o.a) << 4, 11);
      // DR：TMS 1 0 0，len 位（最后一位 TMS 1），再 1 0；TDO 从第 3 位起
      OpDr:    return tuple3(1 | (128'd3 << (o.len + 2)), zeroExtend(o.a) << 3, o.len + 5);
      // 两次 41 位 dmi 扫描，中间 Update-DR 之后 TMS 1 直接回 Select-DR；第二次的 TDO 从第 48 位起
      default: return tuple3(1 | (128'd7 << 43) | (128'd3 << 88), (zeroExtend(o.a) << 3) | (zeroExtend(o.b) << 48), 91);
    endcase
  endfunction

  function Action fail(Bit#(10) i, Bit#(64) got, Bit#(64) want) = action
    case (i)
@FAILS@
      default: $display("FAIL step %0d: got %h want %h", i, got, want);
    endcase
  endaction;

  Reg#(Bit#(10)) pc     <- mkReg(0);
  Reg#(Bool)     inScan <- mkReg(False);
  Reg#(Bit#(8))  tries  <- mkReg(0);
  Reg#(Bool)     bad    <- mkReg(False);
  Reg#(Bool)     done   <- mkReg(False);
  Reg#(Bit#(32)) cyc    <- mkReg(0);

  rule interp (!going && !done);
    Op o = progAt(pc);
    if (o.kind == OpEnd)
      done <= True;
    else if (o.kind == OpHalf) begin
      halfW.wset(truncate(o.a));
      pc <= pc + 1;
    end else if (o.kind == OpPin) begin
      Bit#(64) got = zeroExtend(pack(o.a == 0 ? d.core.haltreq : d.core.ndmreset));
      if (got != o.want) begin
        fail(pc, got, o.want);
        bad <= True;
      end
      pc <= pc + 1;
    end else if (!inScan) begin
      startW.wset(scanOf(o));
      inScan <= True;
    end else begin
      Bit#(64) got = truncate((tdoQ >> o.off) & ((128'd1 << o.w) - 1)) & o.mask;
      Bool ok = got == o.want;
      if (o.kind == OpPoll && !ok && tries < 60) begin
        tries <= tries + 1;
        startW.wset(scanOf(o));
      end else begin
        if (!ok) begin
          fail(pc, got, o.want);
          bad <= True;
        end
        tries  <= 0;
        inScan <= False;
        pc     <= pc + 1;
      end
    end
  endrule

  rule count;
    cyc <= cyc + 1;
    if (cyc > 3000000) begin
      $display("TIMEOUT");
      $finish(1);
    end
  endrule

  rule fin (done);
    if (bad || badHw) $display("FAILED");
    else $display("PASS rvdbg: @VERDICT@");
    $finish((bad || badHw) ? 1 : 0);
  endrule
endmodule

endpackage
'''

text = (TEMPLATE.replace("@L@", label)
        .replace("@PARAMS@", f"{bank}, {maker}, {part}, {rev}")
        .replace("@PROG@", "\n".join(arms))
        .replace("@FAILS@", "\n".join(fails))
        .replace("@VERDICT@", verdict))
(out / f"Rvdbg{label}Tb.bsv").write_text(text, encoding="utf-8")
print(f"  rvdbg 行为测试台就位：{len(prog)} 步，IDCODE {IDCODE:08X}，标签 {label or '（空）'}")

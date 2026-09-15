package Rvdbg;

// rvdbg 的顶层：JTAG DTM 接调试模块。对外是 JTAG 四根线与给核的口，DMI 留在里面。
// 两层照 imsic、rom：mkRvdbgOf 只要一个 IDCODE；mkRvdbg 是息壤例化的那一层，
// 厂商号、零件号、修订号是清单的整数参数，与 archinfo 同名同范围，同一颗芯片两处报的是同一个数

import RegIf::*;
import Jep106::*;
import Dm::*;
import Dtm::*;

// 息壤生成的中立顶层只 import 这个包，引脚类型 Jtag、DmCore 定义在 Dtm、Dm 里，import 不会转出，
// 要在这里再导出一次（BSV_lang.tex「Export」：一有 export 语句就只导出列出来的，所以本包自己的也要列）
export Jtag(..);
export DmCore(..);
export RvdbgCfg(..);
export RvdbgCore(..);
export RvdbgIfc(..);
export mkRvdbgOf;
export mkRvdbg;

typedef struct {
  Bit#(0) none;
} RvdbgCfg;

interface RvdbgCore;
  interface Jtag   jtag;
  interface DmCore core;
endinterface

// 没有控制口（ctrl.shape: none），aw、dw 只是息壤例化写法要的类型参数；其余参数按清单声明的顺序
interface RvdbgIfc#(numeric type aw, numeric type dw,
                    numeric type bank, numeric type maker, numeric type part, numeric type rev);
  interface Jtag   jtag;
  interface DmCore core;
endinterface

module mkRvdbgOf#(Bit#(32) idcode)(RvdbgCore);
  DmIfc dm <- mkDm;
  Jtag  jt <- mkDtm(dm.dmi, idcode);

  interface jtag = jt;
  interface core = dm.core;
endmodule

module mkRvdbg#(RvdbgCfg cfg)(RvdbgIfc#(aw, dw, bank, maker, part, rev));
  Jep106    id = jep106(valueOf(bank), valueOf(maker));
  RvdbgCore c <- mkRvdbgOf(idcode(id, fromInteger(valueOf(rev)), fromInteger(valueOf(part))));

  interface jtag = c.jtag;
  interface core = c.core;
endmodule

endpackage

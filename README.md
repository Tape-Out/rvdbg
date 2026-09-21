# rvdbg

External debug for a RISC-V hart: a Debug Module and a JTAG Debug Transport Module.

![maturity](https://img.shields.io/badge/maturity-simulated-yellow) ![license](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0%20OR%20MulanPSL--2.0-blue)

Part of the [Tape-Out](https://github.com/Tape-Out) IP library: Bluespec IP over the
bus-neutral contracts in [`hwcore`](https://github.com/Tape-Out/hwcore), assembled by
[`xirang`](https://github.com/Tape-Out/xirang). Maturity runs `planned` -> `simulated` ->
`fpga-proven` -> `asic-ready` -> `silicon-proven`.

## Status

Simulated. The Debug Module and the JTAG DTM work together in simulation against a behavioural hart model. The `debug` feature of [`hart`](https://github.com/Tape-Out/hart) that answers them is still to come.

Behaviour follows chapters 3 and 6 of the RISC-V Debug Specification 1.0 (ratified 2025-02-21).

| Port | Who uses it | Content |
| :-- | :-- | :-- |
| `jtag` | the debugger | `tck`, `tms`, `tdi`, `tdo`; a 5-bit IR with IDCODE (0x01), `dtmcs` (0x10) and `dmi` (0x11), every other instruction selects BYPASS |
| `core.haltreq`, `core.resumereq` | the hart | halt and resume requests as levels |
| `core.status` | the hart | whether the hart is halted, every cycle |
| `core.regs` | the hart | Access Register: the address is `regno`, the data is `data0`; an error answer means the hart does not have that register |
| `core.mem` | the hart | Access Memory: the address is `data1`, the data is `data0`; an error answer is a bus error |
| `core.ndmreset` | the platform | reset for everything except the Debug Module |

- One hart; `hartsel` is WARL 0.
- The Debug Module hands abstract commands to the hart through `core.regs` and `core.mem` instead of running a debug ROM on it. Access Register takes any `regno` with `aarsize` 2; Access Memory is 32-bit and physical, with `aampostincrement`.
- There is no program buffer, so `postexec` and Quick Access answer `cmderr` 2.
- The TAP runs on the system clock and oversamples TCK, TMS and TDI. The TCK period must be at least 8 system clock cycles.
- A DMI access completes in its Update-DR cycle, so `dmistat` always reads 0 and `idle` is 0.

Not implemented yet:

- the program buffer, Quick Access, system bus access, triggers and single step;
- halt groups, hart arrays, more than one hart, authentication, `hartreset` and halt-on-reset;
- a TAP clocked by TCK itself.

## References

See [NOTICE](NOTICE).

## License

任选其一：

- [MIT](LICENSE-MIT)
- [Apache 2.0](LICENSE-APACHE)
- [木兰宽松许可证 第2版](LICENSE-MULAN)

`SPDX-License-Identifier: MIT OR Apache-2.0 OR MulanPSL-2.0`

除非另行说明，你提交的贡献按上述三者同时授权，不附加其他条件。

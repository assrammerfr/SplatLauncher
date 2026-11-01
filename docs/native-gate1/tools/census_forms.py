#!/usr/bin/env python3
"""Census distinct op_str shapes for every gap-list mnemonic across sdk/main,
so _simd/_fpvec implementations target real forms, not guesses."""
import sys, os, re, json, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "recomp"))

import gen  # noqa
import capstone  # noqa

md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)

GAPS = set("""addv bif bsl cmeq cmgt cmlt cnt dup ext fabd fabs fadd fcmgt fcvtzs
fmaxp fmla fmls fmov fmul fneg fsub ld1 ld1r ld2 ld4 mvni rev64 scvtf shl smax
smin sshll st1 st2 tbl ushl ushll uzp1 zip1 zip2 aesd aese aesimc cmhi cmtst
ld3 ld3r rev16 rev32 saddlp shadd shrn smlal smull2 sqadd sqdmulh sqrdmulh
sqrshrn sqshrn sqsub sqxtn stg umaxv umnegl uqxtn addp bic bit clz faddp fcmeq
fcmge fcmle fcmlt fdiv fmax fmaxnmv fmin fminnmv fminp frsqrts fsqrt ldaxp ldr
orr shll smov sqincd sqxtun sshr uaddw umov umull2 ushll2 ushr uzp2 xtn
shsub uhadd uhsub urhadd srshl sqshl uqshl sqshlu sqrshl uqrshl ins tbx
saddlv uaddlv saddw2 uaddw2 ssubw usubw smlal2 umlal2 pmull pmul uadalp sadalp
fnm fms frintn frintp frintm frintz frinta frintx frinti fcsel
fcvtas fcvtau fcvtms fcvtmu fcvtns fcvtnu fcvtps fcvtpu frecpe frsqrte fmulx
faddp fcmgt fcmeq ldnp stnp sqshrn sqrshrn uqshrn uqrshrn rshrn sli sri
sqabs sqneg sshr ushr srshl xtn2 sqxtn2 uqxtn2 sqxtun2 shrn2 shll2
fmul fdiv fmaxnm fminnm fmla fmls sqdmlal sqdmlsl smlal smlsl
uzp trn zip dupr exts cas swp ldadd ldclr ldeor ldset
cnth sqincb sqinch sqincw sqinct sqdecb fcvtl fcvtn fcvtn2 fcvtl2
addhn raddhn addhn2 raddhn2 subhn2
saddl2 uaddl2 sqrdmulh sqrshl uqrshl
pmull2 abs neg sqneg sqabs
sqdmull sqdmulh sqdmlal sqdmlsl
shl sqshlu sshl ushl
ushr sqrshr urshr ssra ursra srsra
sqshl sqshlu uqshl sqshlu
sqadd uqadd sqsub uqsub
sqshrn sqrshrn uqshrn uqrshrn
sqxtn uqxtn sqxtun xtn
shrn rshrn
smull umull smull2 umull2
smlal umlal smull2 smlsl smlsl2 sqdmull sqdmull2
saddl uaddl saddw uaddw ssubw usubw
saddlp uaddlp saddlv uaddlv
smaxp umaxp sminp uminp addp
smaxv umaxv sminv uminv addv
fmaxp fminp fmaxnmv fminnmv faddp
zip1 zip2 uzp1 uzp2 trn1 trn2
cnt neg not mvn tst
cls clz rev rev16 rev32 rev64
rbit ext
dup ins umov smov mov
tbl tbx
and orr eor bic orn
add sub cmeq cmge cmgt cmhi cmhs cmle cmlt cmtst
mul mla mls sqdmulh sqrdmulh
sqadd uqadd sqsub uqsub
shadd uhadd shsub uhsub
sqrshl uqrshl srshl urshl
sqshl uqshl sqshlu
aese aesd aesimc aesmc
pmull pmul pmull2
fadd fsub fmul fdiv fmax fmin fmaxnm fminnm fabd fmulx frecps frsqrts
fcmge fcmgt fcmeq fcmle fcmlt
fmla fmls fnm fms
faddp fmaxp fminp
scvtf ucvtf fcvtzs fcvtzu fcvtas fcvtau fcvtms fcvtmu fcvtns fcvtnu fcvtps fcvtpu
frecpe frsqrte
fmov fabs fneg fsqrt frintn frintp frintm frintz frinta frintx frinti
fcsel fcvt
mvni movi fmov
sqabs sqneg
stg st2g stzg stz2g ldg
strb ldrb strh ldrh
add b bic bl br cbnz cbz
smov umov ins dup
ld1 ld2 ld3 ld4 ld1r ld2r ld3r ld4r st1 st2 st3 st4 ldnp stnp
ldaxp stlxp ldxp stxp
sqincd sqincb sqincw sqinch sqinct
shll shll2 ushll ushll2 sshll sshll2
sxtl sxtl2 uxtl uxtl2
xtn xtn2 sqxtn sqxtn2 uqxtn uqxtn2 sqxtun sqxtun2
shrn shrn2 rshrn sqshrn sqshrn2 sqrshrn sqrshrn2 uqshrn uqshrn2 uqrshrn uqrshrn2
addhn addhn2 raddhn raddhn2 subhn subhn2
sqrdmulh sqdmulh
fdivr
""".split())

REG = re.compile(r"\b([vxwqdshq])(\d+)\b")
NUM = re.compile(r"#-?\d+")
LANE = re.compile(r"\.(\w)(\[\d+\])")

def norm(op):
    s = REG.sub(lambda m: m.group(1) + "N", op)
    return s

shapes = collections.defaultdict(collections.Counter)

for mod_name in gen.MODULES:
    mod = gen.load_module(mod_name)
    t = mod["nso"].segs[0]
    text = bytes(mod["nso"].mem[t["mem_off"]])
    off = 0
    n = len(text)
    while off + 4 <= n:
        # decode a window, advance by decoded length (capstone stops at undecodable)
        got = list(md.disasm(text[off:off + 16], mod["base"] + off))
        if not got:
            off += 4
            continue
        i = got[0]
        if i.size == 4 and i.mnemonic in GAPS:
            shapes[i.mnemonic][norm(i.op_str)] += 1
        off += 4

out = {}
for m, c in sorted(shapes.items()):
    out[m] = [[s, n2] for s, n2 in c.most_common(14)]

dst = os.path.join(HERE, "gap_shapes.json")
with open(dst, "w") as f:
    json.dump(out, f, indent=1)

# compact print
for m in sorted(out):
    print(f"== {m} ({len(out[m])} shapes)")
    for s, cnt in out[m]:
        print(f"   {cnt:6d}  {s}")
print("written", dst, os.path.getsize(dst))

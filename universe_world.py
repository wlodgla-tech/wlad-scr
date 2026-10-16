"""
universe_world.py
==================
Топові європейські компанії (локальні біржі, формат Yahoo) і азійські
компанії, що торгуються в США у вигляді ADR (їх легше купити, ніж акції на
токійській чи гонконзькій біржі). Список складений вручну; тікер, який
Yahoo не знає, бектест/бот просто пропускає.
"""

def _sfx(sfx, names):
    return [n + sfx for n in names.split()]

EUROPE = (
    _sfx(".DE", "SAP SIE ALV DTE MBG BMW VOW3 IFX ADS MUV2 DB1 BAS BAYN DHL RHM HEN3 SHL ENR VNA EOAN RWE DBK HEI CBK MRK FRE BEI 1COV ZAL QIA SY1 PAH3 CON MTX HNR1 FME")
    + _sfx(".PA", "MC OR RMS TTE SAN AIR SU AI BNP EL CS KER SAF DG RI CAP STMPA ENGI ORA ACA GLE BN VIE LR PUB SGO ML DSY HO TEP EN")
    + _sfx(".AS", "ASML PRX INGA AD HEIA PHIA WKL ADYEN AKZA NN ASM BESI UNA MT KPN IMCD")
    + _sfx(".MC", "ITX SAN IBE BBVA TEF REP AMS FER CABK ELE")
    + _sfx(".MI", "ENEL ISP UCG ENI G RACE STLAM MONC PRY TRN SRG LDO")
    + _sfx(".SW", "NESN NOVN ROG UBSG ZURN ABBN CFR LONN SIKA GIVN ALC HOLN SREN GEBN PGHN LOGN SCMN KNIN")
    + _sfx(".CO", "NOVO-B DSV ORSTED MAERSK-B VWS CARL-B COLO-B DANSKE PNDORA")
    + _sfx(".ST", "ATCO-A VOLV-B ERIC-B INVE-B HM-B SAND ASSA-B ABB ESSITY-B SEB-A SWED-A EVO SHB-A ALFA EQT")
    + _sfx(".OL", "EQNR DNB TEL MOWI ORK YAR")
    + _sfx(".HE", "NOKIA NESTE SAMPO KNEBV UPM FORTUM STERV")
    + _sfx(".BR", "ABI UCB KBC SOLB GBLB ARGX")
)

ASIA_ADR = (
    "TSM BABA PDD JD BIDU NIO LI XPEV SONY TM HMC MUFG SMFG NTES TCOM YUMC INFY HDB IBN "
    "WIT SE CPNG TME BILI UMC ASX KB SHG SKM KT NMR TAK"
).split()

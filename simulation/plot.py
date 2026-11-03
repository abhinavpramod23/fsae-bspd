import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, os
H=os.path.dirname(os.path.abspath(__file__))
def load(n):
    rows=[l.split() for l in open(f'{H}/runs/{n}.dat')]; h=[x.lower() for x in rows[0]]
    d={k:[] for k in h}
    for r in rows[1:]:
        for k,v in zip(h,r): d[k].append(float(v))
    return d
fig,ax=plt.subplots(3,1,figsize=(10,9))
d=load('s05_both_trip_latch'); t=[x*1000 for x in d['time']]
a=ax[0]; a.plot(t,d['v(brk)'],label='BRK (40 bar)'); a.plot(t,d['v(bth)'],'--',label='BRK_TH (24 bar, <=30 bar rule)'); a.plot(t,d['v(tps)'],label='TPS (throttle body)'); a.plot(t,d['v(tth)'],'--',label='TPS_TH')
a.axvspan(500,1500,color='0.9'); a.set_ylabel('V'); a.set_title('Hard brake + throttle from 500 ms, released at 1500 ms'); a.legend(loc='upper right',fontsize=8); a.set_xlim(0,3000)
a=ax[1]; a.plot(t,d['v(ct)'],label='CT (timer cap)'); a.plot(t,d['v(vm)'],'--',label='2.5 V ref'); a.plot(t,d['v(latch)'],label='LATCH'); a.plot(t,d['v(ok)'],label='OK (relay drive)')
a.axvline(827,color='r',lw=0.8); a.text(840,4.2,'SDC opens @ +327 ms, stays latched',color='r',fontsize=9); a.set_ylabel('V'); a.legend(loc='center right',fontsize=8); a.set_xlim(0,3000)
d2=load('s06_f_tw'); t2=[x*1000 for x in d2['time']]
a=ax[2]; a.plot(t2,d2['v(tps)'],label='TPS (J7 signal unplugged @ 500 ms)'); a.plot(t2,d2['v(latch)'],label='LATCH'); a.plot(t2,d2['v(ok)'],label='OK'); a.axhline(4.75,ls=':',color='k',lw=0.7)
a.set_xlim(300,900); a.set_xlabel('time (ms)'); a.set_ylabel('V'); a.set_title('Sensor signal disconnected (T 11.6.7 / T 11.9.2) -> SDC opens in 81 ms'); a.legend(loc='center right',fontsize=8)
plt.tight_layout(); plt.savefig(f'{H}/../docs/images/simulation.png',dpi=130)

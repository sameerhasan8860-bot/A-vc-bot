"""Real-time stereo PCM port of the supplied VoiceAmp AudioProcessor."""
from dataclasses import asdict, dataclass
import math
import numpy as np

@dataclass
class DSPSettings:
    enabled: bool=True
    amplification_db: float=20.0
    gain_percent: float=50.0
    bass_percent: float=50.0
    treble_percent: float=50.0
    echo_percent: float=0.0
    distortion_percent: float=0.0
    def validate(self):
        for k in ("amplification_db","gain_percent","bass_percent","treble_percent","echo_percent","distortion_percent"):
            x=float(getattr(self,k)); hi=200.0 if k=="amplification_db" else 100.0
            if not math.isfinite(x) or not 0<=x<=hi: raise ValueError(f"{k} must be 0-{hi:g}")
            setattr(self,k,x)
    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls,d):
        o=cls(**{k:v for k,v in (d or {}).items() if k in cls.__dataclass_fields__}); o.validate(); return o

class Biquad:
    def __init__(self,rate,cutoff,kind):
        self.rate,self.cutoff,self.kind=rate,cutoff,kind; self.reset(); self.set_gain(0)
    def reset(self): self.x1=self.x2=self.y1=self.y2=0.0
    def set_gain(self,db):
        a=10**(db/40); w=2*math.pi*self.cutoff/self.rate; c=math.cos(w); sn=math.sin(w)
        alpha=sn/2*math.sqrt(2); q=math.sqrt(a)*2*alpha
        if self.kind=="low":
            b0=((a+1)-(a-1)*c+q)*a; b1=2*a*((a-1)-(a+1)*c); b2=((a+1)-(a-1)*c-q)*a
            a0=a+1+(a-1)*c+q; a1=-2*((a-1)+(a+1)*c); a2=a+1+(a-1)*c-q
        else:
            b0=(a+1+(a-1)*c+q)*a; b1=-2*a*((a-1)+(a+1)*c); b2=((a+1)+(a-1)*c-q)*a
            a0=a+1-(a-1)*c+q; a1=2*((a-1)-(a+1)*c); a2=a+1-(a-1)*c-q
        self.coef=(b0/a0,b1/a0,b2/a0,a1/a0,a2/a0)
    def process(self,x):
        b0,b1,b2,a1,a2=self.coef; y=np.empty(len(x),dtype=np.float64)
        x1,x2,y1,y2=self.x1,self.x2,self.y1,self.y2
        for i,v in enumerate(x):
            z=b0*float(v)+b1*x1+b2*x2-a1*y1-a2*y2; y[i]=z
            x2,x1,y2,y1=x1,float(v),y1,z
        self.x1,self.x2,self.y1,self.y2=x1,x2,y1,y2
        return y

class AudioDSP:
    def __init__(self,sample_rate=48000,channels=2,settings=None):
        self.rate,self.channels=sample_rate,channels; self.settings=settings or DSPSettings(); self.settings.validate()
        self.bass=[Biquad(sample_rate,200,"low") for _ in range(channels)]
        self.treble=[Biquad(sample_rate,3000,"high") for _ in range(channels)]
        self.echo=np.zeros((channels,int(sample_rate*.4))); self.echo_pos=0; self._filters()
    def _filters(self):
        for f in self.bass: f.set_gain((self.settings.bass_percent-50)/50*12)
        for f in self.treble: f.set_gain((self.settings.treble_percent-50)/50*12)
    def update(self,**kw):
        d=self.settings.to_dict(); d.update(kw); self.settings=DSPSettings.from_dict(d); self._filters()
    def process(self,pcm):
        a=np.frombuffer(pcm,dtype="<i2"); n=len(a)-(len(a)%self.channels)
        if not n:return pcm
        if not self.settings.enabled:return pcm
        x=a[:n].astype(np.float64).reshape(-1,self.channels); out=np.empty_like(x); s=self.settings
        e=s.echo_percent/100; gain=s.gain_percent/50*10**(s.amplification_db/20); drive=s.distortion_percent/100
        delay=self.echo.shape[1]//2
        for ch in range(self.channels):
            z=self.treble[ch].process(self.bass[ch].process(x[:,ch]))
            if e>.001:
                ix=(self.echo_pos+np.arange(len(z)))%self.echo.shape[1]; delayed=self.echo[ch,(ix-delay)%self.echo.shape[1]]
                self.echo[ch,ix]=np.clip(z+.35*e*delayed,-32768,32767); z=(1-e)*z+e*delayed
            z*=gain
            if drive>.001: z=(1-drive)*z+32768*np.tanh((z/32768)*(30*drive+1))*drive
            mag=np.abs(z); lim=30000+2767*np.tanh(np.maximum(mag-30000,0)/20000)
            out[:,ch]=np.where(mag<=30000,z,np.sign(z)*lim)
        if e>.001:self.echo_pos=(self.echo_pos+len(x))%self.echo.shape[1]
        return np.clip(out,-32768,32767).astype("<i2").tobytes()

"""Create a deterministic mellow instrumental matching src/film.json."""
from pathlib import Path
import json, wave
import numpy as np
ROOT=Path(__file__).resolve().parent
film=json.loads((ROOT/'src/film.json').read_text(encoding='utf-8'))
SR=48000
frames=sum(s['duration'] for s in film['shots'])-film['overlap']*(len(film['shots'])-1)
DURATION=frames/film['fps']
mix=np.zeros((round(SR*DURATION),2),dtype=np.float64)
def hz(n): return 440*2**((n-69)/12)
def add(signal,start,gain=1,pan=0):
    offset=round(start*SR)
    length=min(len(signal),len(mix)-offset)
    if offset<0 or length<=0:return
    gains=np.sqrt(np.array([1-pan,1+pan])/2)
    mix[offset:offset+length]+=signal[:length,None]*gain*gains
chords=[[50,57,60,64],[55,59,62,66],[52,59,62,67],[48,55,59,62],[53,60,64,69],[50,57,60,64]]
start=0
for section,shot in enumerate(film['shots']):
    span=(shot['duration']-(film['overlap'] if section<len(film['shots'])-1 else 0))/film['fps']
    chord=chords[section%len(chords)]
    t=np.arange(round((span+.7)*SR))/SR
    env=np.minimum(t/.6,1)*np.minimum(np.maximum(span+.7-t,0)/1.1,1)
    pad=sum(np.sin(2*np.pi*hz(n)*t+.08*np.sin(2*np.pi*.3*t)) for n in chord)/4
    add(pad*env,start,.12,-.16)
    for step in range(6):
        t=np.arange(round(2.1*SR))/SR
        freq=hz(chord[[0,2,1,3,2,1][step]]+12)
        piano=(np.sin(2*np.pi*freq*t)+.28*np.sin(2*np.pi*freq*2*t)*np.exp(-t*3)+.09*np.sin(2*np.pi*freq*3*t)*np.exp(-t*5))
        piano*=np.minimum(t/.008,1)*np.exp(-t*2.6)
        at=start+step*span/6
        add(piano,at,.19,.32*np.sin(step+section))
        add(piano,at+span/12,.037,-.32*np.sin(step+section))
    t=np.arange(round(span*SR))/SR
    bass=np.sin(2*np.pi*hz(chord[0]-12)*t)*np.minimum(t/.08,1)*np.exp(-t*1.3)
    add(bass,start,.16)
    start+=span
mix=np.tanh(mix)
fade_in=min(round(.45*SR),len(mix));fade_out=min(round(1.4*SR),len(mix))
mix[:fade_in]*=np.linspace(0,1,fade_in)[:,None]
mix[-fade_out:]*=np.linspace(1,0,fade_out)[:,None]
mix*=.78/max(float(np.abs(mix).max()),.001)
pcm=(mix*32767).astype('<i2')
target=ROOT/'public/music.wav'
target.parent.mkdir(parents=True,exist_ok=True)
with wave.open(str(target),'wb') as w:
    w.setnchannels(2);w.setsampwidth(2);w.setframerate(SR);w.writeframes(pcm.tobytes())
print(json.dumps({'seconds':DURATION,'sample_rate':SR,'peak_dbfs':float(20*np.log10(np.abs(pcm.astype(float)).max()/32768)),'rms_dbfs':float(20*np.log10(np.sqrt(np.mean((pcm.astype(float)/32768)**2))))}))

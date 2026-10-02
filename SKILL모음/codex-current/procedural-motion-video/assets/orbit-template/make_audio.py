"""Synthesize a deterministic 12-second electronic score; no external samples."""
from pathlib import Path
import json
import wave
import numpy as np

SR = 48000
DURATION = 12
rng = np.random.default_rng(913)
mix = np.zeros((SR * DURATION, 2), dtype=np.float64)

def add(signal, start, gain=1.0, pan=0.0):
    offset = int(start * SR)
    n = min(len(signal), len(mix) - offset)
    if n <= 0:
        return
    mix[offset:offset+n, 0] += signal[:n] * gain * np.sqrt((1-pan)/2)
    mix[offset:offset+n, 1] += signal[:n] * gain * np.sqrt((1+pan)/2)

def note(midi):
    return 440 * 2 ** ((midi - 69) / 12)

# A restrained 120 BPM pulse, with scene changes on bar boundaries.
for beat in range(23):
    start = beat * .5
    t = np.arange(int(.32 * SR)) / SR
    phase = 2*np.pi*(47*t + 105*.026*(1-np.exp(-t/.026)))
    kick = np.sin(phase) * np.exp(-t*15) * (1-np.exp(-t*700))
    add(kick, start, .57)
    if beat % 2:
        t = np.arange(int(.16*SR))/SR
        noise = rng.normal(0,1,len(t))
        clap = (noise - np.roll(noise,1)) * np.exp(-t*35) * (1-np.exp(-t*500))
        add(clap, start, .06, .08)
    t = np.arange(int(.06*SR))/SR
    noise = rng.normal(0,1,len(t))
    hat = (noise-np.roll(noise,1))*np.exp(-t*80)
    add(hat, start+.25, .023, -.28 if beat%2 else .28)

chords = [[53,60,65,68], [56,60,63,68], [51,58,63,67]]
for section, chord in enumerate(chords):
    t = np.arange(int(4.0*SR))/SR
    envelope = np.minimum(t/.18, 1) * np.minimum((4-t)/.48,1)
    pad = sum(np.sin(2*np.pi*note(n)*t + .23*np.sin(2*np.pi*.35*t)) for n in chord)/len(chord)
    add(pad*envelope, section*4, .12, -.2)
    for step in range(16):
        n = chord[[0,2,1,3,2,1,3,2][step%8]]+12
        t = np.arange(int(.7*SR))/SR
        pluck = (np.sin(2*np.pi*note(n)*t)+.24*np.sin(4*np.pi*note(n)*t))*np.exp(-t*7)*(1-np.exp(-t*320))
        add(pluck, section*4+step*.25, .13, .35*np.sin(step))
        add(pluck, section*4+step*.25+.1875, .037, -.35*np.sin(step))
    for beat in range(8):
        t = np.arange(int(.36*SR))/SR
        bass = (np.sin(2*np.pi*note(chord[0]-12)*t)+.13*np.sin(4*np.pi*note(chord[0]-12)*t))*np.exp(-t*8)*np.minimum(t/.013,1)
        add(bass,section*4+beat*.5+.06,.24)

t = np.arange(SR)/SR
bell = sum(np.sin(2*np.pi*note(n)*t) for n in [63,67,70])/3*np.exp(-t*5)*np.minimum(t/.01,1)
add(bell,11,.17)
mix = np.tanh(mix*1.12)
mix[:960] *= np.linspace(0,1,960)[:,None]
mix[-int(.65*SR):] *= np.linspace(1,0,int(.65*SR))[:,None]
mix *= .88 / max(np.max(np.abs(mix)), .001)
target = Path(__file__).parent/'public'/'orbit-soundtrack.wav'
target.parent.mkdir(exist_ok=True)
with wave.open(str(target),'wb') as out:
    out.setnchannels(2)
    out.setsampwidth(2)
    out.setframerate(SR)
    out.writeframes((mix*32767).astype('<i2').tobytes())
print(json.dumps({'file':str(target),'duration_seconds':DURATION,'sample_rate':SR,'channels':2,'peak_dbfs':round(float(20*np.log10(np.max(np.abs(mix)))),2),'rms_dbfs':round(float(20*np.log10(np.sqrt(np.mean(mix**2)))),2)},ensure_ascii=False))

"""Isolated, offline Whisper CPU inference. PCM16 mono 16 kHz arrives on stdin."""

import json
import sys


def main():
    import numpy as np
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

    pcm = sys.stdin.buffer.read(640001)
    if not pcm or len(pcm) > 640000 or len(pcm) % 2:
        raise ValueError("Expected at most twenty seconds of PCM16")
    torch.set_num_threads(2)
    model_path = sys.argv[1]
    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        model_path, local_files_only=True
    ).eval()
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    inputs = processor(samples, sampling_rate=16000, return_tensors="pt")
    with torch.inference_mode():
        tokens = model.generate(inputs.input_features, max_new_tokens=128)
    text = processor.batch_decode(tokens, skip_special_tokens=True)[0]
    print(json.dumps({"text": text}))


if __name__ == "__main__":
    main()

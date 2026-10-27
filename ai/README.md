# DVS Jetson Student AI Lab

Students develop preprocessing, event-based classification, fall-detection research, storage, and LLM/RAG reporting independently of the Jetson kernel driver.

The kernel module, device tree, `sdk/` decoder, and `app/dvs_streamer/` remain maintained upstream integration layers. A validated adapter should expose **decoded** DVS events as `timestamp_us,x,y,polarity` rows or a versioned binary schema. Do not feed RAW2 wire bytes directly to the LLM.

The code under `ai/dvs_lab/` is a small CSV → time-window → SQLite → factual reporting baseline. It is NOT a trained detection model, not medical/safety qualified, and not connected to the live Jetson stream.

Run from the `ai` directory:
```bash
cd ai
python3 -m dvs_lab.cli ingest data/samples/sample_events.csv
python3 -m dvs_lab.cli report
python3 -m unittest discover -s tests -v
```

Student workstreams: `preprocessing/`, `models/event_classifier/`, `models/fall_detection/`, `llm/rag/`, `llm/reporting/`, `database/`, and `apps/`. Use topic branches, review PRs, and keep identifiable recordings or large raw streams out of Git history.

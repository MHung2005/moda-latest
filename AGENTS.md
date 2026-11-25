# AGENTS.md — Moda weighbridge traffic pipeline (JetPack 6 / DeepStream 7.1)

Canonical reference for coding agents. Repo: `/home/hicas/hung_lm/moda-jp6` on the Jetson `hicas-desktop`
(aarch64, L4T R36.4 = JetPack 6.2). Git branch `main`; history: `3428528` (full flow incl. cargo/"thùng hàng"),
`e56ae7e` (cargo analyzer removed). No CI, no Makefile. Human docs/log messages are Vietnamese; code/comments English.
Lane naming: `{in,out}_gate1_lane{1,2}` everywhere (renamed from the legacy `*_land*` on 2026-11-18, including Kafka
`trigger_code`/`analysis_code` values, env var names, compose services, model files, both SQLite DBs and `runtime/`
images; pre-rename copies of the DBs and `.env` files are in `config_ui/data/pre-lane-rename-20261118-091445/`).
Never reintroduce `land`: it is part of every contract below.

---

## 1. System Overview & Tech Stack

Edge vision system for a truck weighbridge at Gate 1 (2 lanes × in/out = 4 lane cameras + 1 camera filming the scale's
LCD). Event-driven, three independently deployed Docker Compose projects joined only by Kafka topics, file paths and
generated config files. (1) `traffic_core` — one DeepStream process per lane camera detects the vehicle, tracks it,
reads its licence plate, saves a JPEG and publishes a trigger to Kafka `VehicleDetection_Gate1`. (2) `post_analysis`
(`weight_reader`) consumes triggers, reads the weighbridge display via a YOLO digit model served by Triton and publishes
`AnalysisResult_Gate1` (failures → `AnalysisFailure_Gate1`). (3) `config_ui` — FastAPI + React admin UI whose SQLite DB is
the source of truth for all config files; it regenerates them and recreates containers through the Docker socket; a
sibling `results_recorder` stores the three topics in a results SQLite for the UI's results pages.

| Area | Tech (pinned versions) |
|---|---|
| Detector runtime | `nvcr.io/nvidia/deepstream:7.1-triton-multiarch`, Python 3.10, GStreamer (`gi`), `pyds` 1.2.0 (downloaded at build from GitHub release), `numpy<2`, `kafka-python==2.0.2`, `opencv-python-headless==4.10.0.84`, TensorRT 10, CUDA 12.6 |
| Detector models | DeepStream `nvinfer` ×3 (YOLO ONNX → TRT FP16 engines via `trtexec`), custom bbox parser `NvDsInferParseYolo` (DeepStream-Yolo, rebuilt in image), `nvtracker` NvDCF (`libnvds_nvmultiobjecttracker.so`) |
| Analyzer runtime | `ultralytics/ultralytics:8.3.153-jetson-jetpack6` + `kafka-python==2.0.2`, `tritonclient[http]==2.42.0`; Ultralytics `YOLO("http://triton:8000/tram_can")` |
| Model server | `nvcr.io/nvidia/tritonserver:24.01-py3-igpu`, repo `models/triton`, single model `tram_can` (ONNX) |
| Broker | Kafka (service `kafka` in `compose.yaml`, container `moda-kafka`, `apache/kafka:3.8.0`, single-node KRaft, volume `kafka_data`; EXTERNAL listener advertised as `MODA_KAFKA_BOOTSTRAP_SERVERS`, UI-unmanaged; `scripts/stack.sh` starts it first, stops it last), host `<jetson-ip>:29092`; clients pin `api_version=(2,4,0)` |
| Config UI backend | Python 3.11-slim, `fastapi==0.115.6`, `uvicorn==0.32.1`, `pydantic==2.10.4`, `cryptography==44.0.0` (Fernet), `PyYAML==6.0.2`, OpenCV headless, kafka-python; docker CLI 27.3 + compose plugin copied in |
| Config UI frontend | React 18.3.1, TypeScript 5.6.3, Vite 5.4.11, antd 5.22.5, @tanstack/react-query 5.62.8, react-router-dom 6.28.0, konva 9.3.18 / react-konva 18.2.10 (ROI drawing), dayjs |
| Storage | Local filesystem `runtime/` (JPEGs, TRT engines), SQLite (`config_ui/data/moda_config.db`, `moda_results.db`, WAL) |
| Cameras | RTSP (Hikvision URLs `/Streaming/Channels/X01`), TCP transport forced |
| Legacy | `pyds-1.1.8-py3-none-linux_aarch64.whl` (JP5, unused), sibling dir `~/hung_lm/moda` (JP5 deployment). JP5 `.so` builds and `*.bak` files were deleted; `.gitignore` blocks `*.so`, `*.bak`, `*.bak-*`, `*.before-*` |

---

## 2. Repository Map

```text
moda-jp6/
├── traffic_core/                    # Detector package (python3 -m traffic_core --site <json>)
│   ├── __main__.py / cli.py         # argparse --site/--gpu; sets CUDA_VISIBLE_DEVICES BEFORE importing detection
│   ├── config.py                    # load_site_config() -> SiteConfig (stdlib only; also imported by config_ui)
│   ├── deepstream.py                # DeepStreamPipeline: GStreamer graph, RTSP source bins, bus handling, watchdog timer
│   ├── detection.py                 # VehicleDetection(DeepStreamPipeline): pad probes, capture/re-entry logic, publish
│   ├── tracking.py                  # TrackKey(source_id, object_id), TrackState, TrackingRegistry (in-memory)
│   ├── plate.py                     # char→row grouping, plate regex validation, per-position best char, fuzzy match
│   ├── events.py                    # build_vehicle_message(): VehicleDetection_Gate1 payload (legacy contract)
│   ├── kafka.py                     # create_producer(acks=all), publish() = send + future.get(timeout)
│   ├── watchdog.py                  # StallWatchdog (MODA_SOURCE_STALL_SECONDS / MODA_SOURCE_STARTUP_SECONDS) [uncommitted]
│   ├── geometry.py                  # contains_point() ray casting
│   ├── fps.py                       # FpsCounter (logs FPS once per second per stream)
├── post_analysis/                   # Analyzer package (python3 -m post_analysis weight --config <json>)
│   ├── cli.py                       # SIGTERM -> KeyboardInterrupt; only service "weight"
│   ├── config.py                    # load_weight_config() -> WeightConfig (stdlib only; also imported by config_ui)
│   ├── runtime.py                   # run_worker_service(): Kafka consumer, spawn-process workers, OffsetTracker, DLQ
│   ├── weight_reader.py             # process_event(): RTSP read, crop, YOLO digits, WeightAccumulator, publish
│   ├── capture.py                   # open_camera(): cv2.VideoCapture(FFMPEG) with open/read timeouts
│   ├── models.py                    # load_yolo(): Ultralytics YOLO over Triton with startup retries
│   ├── contracts.py                 # build_analysis_result(): copy trigger + analysis_* fields
│   ├── kafka.py                     # consumer (manual commit, latest), producer, decode_event, commit_offsets
├── config/                          # GENERATED by config_ui once it is in use — do not hand-edit then
│   ├── sites/{in,out}_gate1_lane{1,2}.json   # per-lane site config (stem == sites.code; run by the gate service that lists it with --site)
│   ├── post_analysis.json           # topics, weight analyzer settings, crops, analysis codes
│   └── reference_plates/{standard,lane2}.txt # known plates, one per line, for fuzzy best_match
├── models/                          # mounted RO at /home/jetson/models (detector) and /models (Triton: models/triton)
│   ├── object_detection/            # vehicle YOLO: infer_in_lane1.txt, infer.txt (in_lane2), infer_out_lane{1,2}.txt, *.onnx, labels.txt
│   ├── plate_detection_with_logo/   # plate YOLO lp.onnx + infer.txt (gie 2), labels square_lp/long_lp
│   ├── plate_recognition/           # character YOLO26n yolo26n_doc_bien_ds.onnx + labels_yolo26n.txt (gie 3, 31 classes, 640x640, static batch 1); best224.onnx + labels.txt kept for rollback; parser sources compiled into image
│   ├── tracker/config_tracker.txt   # [tracker] keys read by DeepStreamPipeline._configure_tracker; .yml = NvDCF config
│   ├── triton/tram_can/{config.pbtxt(empty), 1/model.onnx}  # weighbridge digit model
│   ├── config_dsanalytics.txt       # unused
│   └── DeepStream-Yolo/, */nvdsinfer_custom_impl_Yolo*/  # vendored upstream; only 4 parser files are built
├── docker/
│   ├── entrypoint.sh                # detector ENTRYPOINT: prepare_engines.py <site> then exec python3 -m traffic_core
│   ├── prepare_engines.py           # trtexec engine builder into /cache/engines, TRT-version stamp, minimal ONNX parser
│   └── yolo_parser.mk               # builds only NvDsInferParseYolo -> /opt/moda/libnvdsinfer_custom_impl_Yolo.so
├── config_ui/
│   ├── Dockerfile                   # node build -> docker CLI -> python:3.11 runtime; ENTRYPOINT python3 -m moda_ui
│   ├── backend/moda_ui/
│   │   ├── __main__.py              # CLI: serve | import [--force] | create-admin | gen-key | generate | record
│   │   ├── main.py                  # create_app(): routers, exception handlers, SPA static, live.reaper task
│   │   ├── api.py                   # /api router (auth, users, cameras, plates, sites, analyzers, models, apply, revisions, services)
│   │   ├── results_api.py           # /api/results (read-only, any logged-in user)
│   │   ├── schema.sql               # config DB schema (source of truth)
│   │   ├── repository.py            # all SQL for config DB; NotFound/Conflict; snapshot export/import
│   │   ├── schemas.py               # Pydantic request models mirroring runtime validation
│   │   ├── generator.py             # DB -> {relative path: file content} (site JSON, plates, post_analysis.json, .env, .env.jetson)
│   │   ├── validation.py            # validates generated files with traffic_core.config / post_analysis.config + compose env wiring
│   │   ├── applier.py               # preview/apply/rollback, backups, atomic write, recreate + health check, revert on failure
│   │   ├── docker_ops.py            # `docker compose ps/up/logs`, restart counts; DETECTOR/ANALYSIS projects; triton unmanaged
│   │   ├── envfile.py               # dotenv parse/update preserving comments; `$`→`$$` escaping
│   │   ├── importer.py              # files -> DB bootstrap (`import`)
│   │   ├── security.py              # PBKDF2 passwords, Fernet camera URL encryption, Fernet session tokens, mask_url
│   │   ├── auth.py                  # cookie `moda_session`, current_user, require_admin
│   │   ├── db.py                    # sqlite connect (WAL, FK on), init_schema (+ remove_cargo cleanup), get_db dependency
│   │   ├── results.py               # results DB schema + record_vehicle/analysis/failure + queries + image path mapping
│   │   ├── recorder.py              # Kafka consumer of 3 topics -> results DB (container results_recorder)
│   │   ├── live.py                  # shared RTSP->MJPEG readers, Hikvision substream preference, idle reaper
│   │   ├── snapshot.py              # one-frame JPEG grab for ROI drawing
│   │   └── settings.py              # Settings from env (MODA_PROJECT_DIR, MODA_CONFIG_DB, ...)
│   ├── backend/tests/test_roundtrip.py  # import -> generate -> runtime loaders accept; apply/restore/rollback with fake docker
│   ├── frontend/src/                # App.tsx routes, api.ts, auth.tsx, pages/*Page.tsx, components/ShapeEditor.tsx (konva ROI)
│   └── data/                        # runtime: moda_config.db, moda_results.db, backups/ (gitignored)
├── tests/                           # unittest: config, events, fps, geometry, plate, post_analysis, tracking, watchdog
├── specs/moda_reverse_spec.md       # requirements (OBS-*/AC-*), matches JetPack 6.2 / DeepStream 7.1, weight analyzer only
├── compose.yaml                     # 4 detector services (image moda-traffic:latest)
├── compose.analysis.yaml            # triton + weight_reader (image moda-analysis:jetpack6)
├── compose.ui.yaml                  # project "moda-config-ui": config_ui + results_recorder
├── Dockerfile / Dockerfile.analysis # detector image / analyzer image
├── scripts/analysis-service.sh      # preflight + start|stop|restart|status|logs|config for compose.analysis.yaml
├── .env.example / .env.jetson.example / .env.ui.example  # templates (real .env* gitignored, contain RTSP creds)
├── pyproject.toml                   # moda-traffic-core 0.1.0; scripts moda-traffic, moda-analysis
└── runtime/                         # gitignored outputs: engines/, vehicle/<site>/<YYYYMMDD>/, weight_result/<YYYYMMDD>/, thung_hang/ (legacy)
```

---

## 3. Core Architecture & End-to-End Flows

### 3.0 Topology

```text
 RTSP lane cam (1 per site, 2 sites per gate)        RTSP weighbridge LCD cam
        │ uridecodebin (TCP)                                   │ cv2 FFMPEG (TCP)
        ▼                                                      │
 ┌─ detector container <site> (compose.yaml) ─┐                 │
 │ streammux 2688x1520 → nvvidconv → RGBA     │                 │
 │ → pgie(vehicle) →[probe: inference zone]   │                 │
 │ → nvtracker NvDCF → sgie(plate)            │                 │
 │ → tgie(chars) →[probe: main logic] → fake  │                 │
 └───────┬───────────────┬────────────────────┘                 │
   JPEG /data/vehicle    │ Kafka VehicleDetection_Gate1 (p=0)   │
   = runtime/vehicle/<site>/YYYYMMDD                            │
                         ▼                                      ▼
            ┌─ weight_reader (compose.analysis.yaml) ── spawn worker per event ─┐
            │ group moda-weight-reader, manual commit                           │
            │ crop LCD → HTTP Triton tram_can → digits → WeightAccumulator      │
            └──────┬─────────────────────────────┬──────────────────────────────┘
     JPEG runtime/weight_result/YYYYMMDD   Kafka AnalysisResult_Gate1 / AnalysisFailure_Gate1
                                                 │ (+ VehicleDetection_Gate1)
                                                 ▼
                    results_recorder → config_ui/data/moda_results.db ← config_ui /api/results
 config_ui ── generates config/*, .env, .env.jetson ── docker compose up --force-recreate (docker.sock)
```

### 3.1 Initialization sequence

**Detector** (`docker compose up -d --build <site>`; one Jetson should start only the sites it owns):
1. `docker/entrypoint.sh` extracts `--site`, runs `docker/prepare_engines.py <site.json>`:
   `drop_stale_engines()` compares `/cache/engines/.tensorrt-version` with `libnvinfer.so.X.Y.Z` → deletes all `*.engine`
   on mismatch; for `vehicle_detection`, `plate_detection`, `plate_recognition` reads the `[property]` of each infer file,
   reads ONNX first input via hand-written protobuf parser, runs `trtexec --onnx --saveEngine=<engine>.<pid>.tmp [--fp16]
   [--min/opt/maxShapes]` (vehicle max batch 4, opt = `batch-size`) then `os.replace`. Logs in `/cache/engines/<stem>.trtexec.log`.
2. `exec python3 -m traffic_core --site ...` → `cli.run`: `load_site_config` (env must contain every `camera_url_envs`
   name + `MODA_KAFKA_BOOTSTRAP_SERVERS`, else `SiteConfigError`) → `os.environ["CUDA_VISIBLE_DEVICES"]` → import
   `detection` (imports `pyds`, `ctypes.CDLL(libnvdsgst_helper.so)`).
3. `VehicleDetection.__init__`: Kafka producer (connects now), `_build_pipeline(camera_urls)`, loads reference plates.
4. `run()` → `_run_pipeline`: probes attached (tracker src → `_filter_inference_objects`, tgie src →
   `_process_inference_results`), `StallWatchdog.from_environment`, `GLib.timeout_add_seconds(5, _check_sources)`,
   `PLAYING`, `loop.run()`. `finally`: `_flush_pending_events`, `producer.flush/close(10)`.

**Analysis** (`./scripts/analysis-service.sh start`): preflight (docker, `nvidia` runtime, `.env.jetson` has non-example
`MODA_KAFKA_BOOTSTRAP_SERVERS`/`MODA_WEIGHT_CAMERA_URL`, `models/triton/tram_can/{1/model.onnx,config.pbtxt}` exist,
`mkdir runtime/weight_result`, `compose config --quiet`) → `up --build triton weight_reader`. `weight_reader`
`depends_on: triton: service_healthy` (`/v2/health/ready`). CLI: `load_weight_config` → `run_worker_service`.

**Config UI** (`docker compose -f compose.ui.yaml --env-file .env.ui up -d`): `create_app` startup → `init_schema`
(also `remove_cargo`) → `live.reaper()` task. One-time: `run --rm config_ui import` then `create-admin <user>`.
`results_recorder` = same image, `command: ["record"]`, `mem_limit: 256m`.

### 3.2 Flow A — Vehicle trigger (detector, per frame)

GStreamer link order (`deepstream.py:_build_pipeline`):
`nvstreammux(2688x1520, batch=len(urls), batched-push-timeout=40000, live-source if rtsp) → queue → nvvideoconvert →
queue → capsfilter(video/x-raw(memory:NVMM),format=RGBA) → queue → pgie → queue → nvtracker → queue → sgie → queue →
tgie → queue → fakesink(qos=0)`. RGBA conversion happens **before** inference so `pyds.get_nvds_buf_surface` works.

```text
pgie (gie-unique-id=1, vehicle; classes front/back/car/other, TARGET class 0)
  └─ tracker src probe _filter_inference_objects:
       foot point = (cx, y2) of each component-1 object; if outside inference_zones[source_id]
       → obj_meta.unique_component_id = 9999  (sgie operate-on-gie-id=1 then skips it)
sgie (gie 2, plate, classes 0 square_lp / 1 long_lp)  →  tgie (gie 3, 31 chars 0-9 + A-Z without I J O Q W, operate-on-class-ids=0;1)
  └─ tgie src probe _process_inference_results, per frame_meta:
     1. watchdog.mark(source_id)
     2. _extract_frame_detections: vehicles (component 1 or 9999, class 0), plates (component 2, parent=vehicle),
        chars (component 3, conf > 0.5, parent.parent=vehicle) → plate.merge_box (rows by height ratio ≥0.8,
        sorted by y then x) → update_vehicle_box → _VehicleObservation(label=rows joined, confidences)
     3. _process_vehicle_observation (skip if conf <= vehicle_confidence):
          state = tracks.ensure(TrackKey(source, object_id)); update_raw_plate (keeps longest);
          if is_valid_license_plate_no_hyphen(label): state.add_plate(...)  (also to active re-entry child)
          if foot ∈ capture_zones[source] and no capture yet: record_capture(frame copy BGR, frame_num,
             UTC ts, started_at=now); reentry_armed=True; pending_events.add(key)
          observe_zone(): outside > EXIT_FRAME_GAP(5) frames after inside → outside_since_capture;
             re-entering consumes the arm → new synthetic TrackKey(source, 90000+n) with parent_key, own capture
     4. _publish_ready_objects(now): pending with now - collection_started_at > plate_collection_seconds →
        _publish_track: select_best_license_plate(per-position max confidence among max-length candidates;
        search=True if any char < plate_character_confidence) → find_best_match(best plate, else raw_plate)
        against reference list (difflib ratio) → _save_image_and_publish → discard from pending; re-arm parent
        (publish exceptions are logged and the key stays pending → retried next frame)
     5. _expire_tracking_data: drop tracks unseen > TRACK_EXPIRY_SECONDS(600) (also drops their pending event)
```
`_save_image_and_publish`: file `<vehicle_image_root>/<YYYYMMDD in UTC+utc_offset_hours>/<time_ns>_<source_id>_<object_id>_<plate|None>_<trigger_code>.jpg`
via `cv2.imwrite`, then `kafka.publish` (blocking `future.get(timeout=publish_timeout_seconds)`) to
`output_topic`, fixed partition `output_partition`.

Shutdown/failure: bus `EOS`/`ERROR` → `_stop_all_sources` + `loop.quit`; per-stream `stream-eos` removes that source
(+`watchdog.forget`), quit when 0 sources. Watchdog stall → log, start `threading.Timer(30, os._exit, (1,))`, quit loop,
`_run_pipeline` raises `RuntimeError("Camera source(s) ... stalled")` → non-zero exit → Docker `restart: unless-stopped`.

### 3.3 Flow B — Weighbridge reading (`post_analysis/runtime.py:run_worker_service`)

```text
main loop (single thread):
  ├─ for each active job: result pipe ready → outcome | deadline (event_timeout_seconds=75) → _stop_job + retry_or_fail
  │                        | process dead without result → failure
  │   success → OffsetTracker.complete;  failure → retry (attempt+1, ready_at = backoff*2^(attempt-2)) or
  │   complete_failure = publish AnalysisFailure_Gate1 THEN mark offset complete
  ├─ start pending items (respect max_workers, ready_at, one active job per routing key)
  ├─ commit_completed(): commit highest contiguous completed offset+1 per partition
  ├─ poll(max_records=10 when supersede_same_key)
  │   per message: tracker.register → decode_event (bad JSON → DLQ) → routing key _lane(): "lane1"/"lane2"
  │   substring of trigger_code (none → DLQ "Unsupported or missing trigger_code")
  │   supersede: set stop_event on the active job (it publishes its value if read >= MIN_STABLE_COUNT=30 times, else DLQ "Superseded by offset N for laneX: <reason>", never retried; killed after cancellation_grace + publish_timeout) + drop pending items with same key → DLQ "Superseded by offset N"
  │   start immediately if capacity else pending
  └─ commit_completed()
worker process (spawn) weight_reader.process_event(config, event, stop_event):
  crop = config.crops[trigger_code] (missing → ValueError) → load_yolo(model_url) → open_camera
  loop ≤ max_seconds(60): read frame (fail → RuntimeError), check crop within frame,
       lcd = frame[y_min:y_max, x_min:x_max]; value = read_digits(model(lcd), confidence)
       (boxes conf ≥ thr, class 0..9, greedy NMS IoU<0.5, sort by x_min, int(join); >100000 → /10)
       None → frame ignored; else accumulator.add; early_stopped → break
  selected = most frequent value in [peak - 3000, peak] (SELECTION_WINDOW; tie → latest reading);
             peak = largest value read ≥ MIN_PEAK_COUNT (2) times, else largest value (guards one-off misreads)
             early stop (drop > drop_threshold for max_drop_count consecutive readings) only ends the loop
  imwrite <output_root>/<YYYYMMDD local>/weight_<trigger_code>_<time.time()>.jpg (the LCD crop)
  publish build_analysis_result(event, analysis_cam_id=1, analysis_code=analysis_codes[trigger_code],
          analysis_image=path, analysis_output=value) with a fresh producer → close
```
Delivery is at-least-once: crash between output publish and offset commit duplicates results. Idempotency key for
consumers: `section_id + analysis_code` (results DB uses `UNIQUE(section_id, analyzer)` upsert).

### 3.4 Flow C — Config apply (`config_ui/backend/moda_ui/applier.py:apply`)

```text
POST /api/apply (admin) ── non-blocking threading.Lock (busy → 409)
 1. preview(): generator.build_files(conn) → {path: content}
       config/sites/<site.code>.json, config/reference_plates/<file_name>, config/post_analysis.json,
       .env (detector: Kafka + purpose=lane camera URLs), .env.jetson (Kafka, Triton, purpose=weight camera URLs)
    validation.validate_files(): write to tempdir, run traffic_core.config.load_site_config / post_analysis.config.
       load_weight_config with env parsed from generated env files; ROI points ≤ 2688x1520; compose service running
       <site code> exists and passes every camera env var → errors → 422, nothing written
    affected_services(): site JSON → that service; plate file → sites using it; post_analysis.json common/weight
       change → weight_reader; changed env keys ∩ service env names/interpolations (triton excluded)
 2. no diff → ok, no revision
 3. _backup(current) → config_ui/data/backups/<YYYYmmdd-HHMMSS>/ + manifest.json (keep 30)
 4. _atomic_write changed files (tmp + os.replace, keeps mode, LF)
 5. _recreate_and_check: only services that already exist in `compose ps -a` →
       `docker compose --project-directory $MODA_PROJECT_DIR -f <file> [--env-file] up -d --no-deps --no-build --force-recreate`
       sleep MODA_UI_HEALTH_WAIT_SECONDS; fail if state != running or RestartCount increased
 6. failure → _restore(old files, delete created ones) → recreate touched services again → revision status=failed
    success → revision status=applied (snapshot_json = full DB snapshot, files_diff with masked URLs)
POST /api/revisions/{id}/rollback: import_snapshot(rev) → apply(); failure → re-import current snapshot;
    success → mark_rolled_back_after(id), new revision forced to 'applied'
```

### 3.5 Flow D — Results recording (`recorder.py:run`)

Consumer of `VehicleDetection_Gate1`, `AnalysisResult_Gate1`, `AnalysisFailure_Gate1` (env overrides
`MODA_RESULTS_{VEHICLE,ANALYSIS,FAILURE}_TOPIC`), group `MODA_RESULTS_GROUP_ID` (default `moda-results-recorder`),
`auto_offset_reset=earliest`, manual commit after each SQLite transaction (poll 200). Reconnect loop every 5 s on
`KafkaError`. `record_analysis` inserts the vehicle row too if missing (result repeats the trigger payload).

---

## 4. Data Models & Storage Layer

### 4.1 In-memory detector state (`traffic_core/tracking.py`)
`TrackKey(source_id, object_id)` (frozen, ordered). `TrackState`: `section_id` (uuid4, becomes Kafka `section_id`),
`plate_values`/`plate_confidences` (valid plates only), `raw_plate` (longest label seen), capture fields
(`capture_image` BGR ndarray, `capture_frame`, `captured_at` ISO UTC `...Z`, `collection_started_at`), re-entry fields
(`reentry_armed`, `outside_since_capture`, `inside_at_frame`, `active_reentry_id`, `parent_key`), `last_seen_at`.
`VehicleDetection.pending_events: set[TrackKey]` = captured, not yet published. Nothing persists across restarts.

Re-entry state machine per track:
```text
[no capture] --foot in capture zone--> [captured, armed, pending]
   --out of zone > 5 frames--> outside_since_capture=True
   --back in zone & armed--> consume arm → child track 90000+n (own section_id, capture, pending)
   child published → parent.active_reentry_id=None, parent.reentry_armed=True
```

### 4.2 Config DB `config_ui/data/moda_config.db` (`schema.sql`, FK ON, WAL) — source of truth

| Table | Keys / constraints | Notes |
|---|---|---|
| `users` | PK id, UNIQUE username, role ∈ admin/viewer | `password_hash` = `pbkdf2_sha256$240000$<salt b64>$<digest b64>` |
| `cameras` | PK id, UNIQUE code, UNIQUE env_name, purpose ∈ lane/weight/cargo | `rtsp_url_enc` Fernet(MODA_CONFIG_SECRET); `frame_width/height` set by snapshot; `purpose=lane` → `.env`, else `.env.jetson` |
| `reference_plate_lists` | PK id, UNIQUE name, UNIQUE file_name | file in `config/reference_plates/` |
| `reference_plates` | FK list_id CASCADE, UNIQUE(list_id, plate) | normalized upper alnum, deduped |
| `sites` | PK id, UNIQUE code (= compose service = JSON stem), FK reference_plate_list_id | thresholds CHECKed like runtime; model paths are container paths |
| `site_sources` | FK site_id CASCADE, FK camera_id, UNIQUE(site_id, source_index 0..3) | `trigger_code` per source |
| `zones` | FK site_source_id CASCADE, UNIQUE(site_source_id, kind ∈ capture/inference) | `points_json` |
| `system_settings` | PK key, JSON value | kafka_bootstrap_servers, triton_url, input/output/dead_letter topics, publish_timeout_seconds, utc_offset_hours |
| `analyzer_settings` | PK(analyzer, key), analyzer ∈ weight/cargo | JSON values, validated by `schemas.WeightSettings` |
| `analyzer_lanes` | PK(analyzer, trigger_code) | `analysis_code`, `crop_x_min/x_max/y_min/y_max`, optional camera_id |
| `config_revisions` | PK id, FK user_id, status ∈ applied/failed/rolled_back | `snapshot_json` full DB export, `files_diff`, `services` JSON, `log` |

`db.remove_cargo()` runs at every `init_schema`: deletes cargo analyzer rows and unused cargo cameras.

### 4.3 Results DB `config_ui/data/moda_results.db` (`results.py:SCHEMA`, override `MODA_RESULTS_DB`)
- `vehicle_events`: UNIQUE `section_id` (upsert), kafka_partition/offset, trigger_code, trigger_cam_id, plate
  (`detected_object_id`), raw_plate, best_match, match_score REAL, requires_search, event_time (`timestamp`), frame_num,
  image_path (relative to `runtime/`), received_at. Indexes: time, (trigger_code, time), plate, (partition, offset).
- `analysis_results`: UNIQUE(section_id, analyzer); analyzer from `ANALYZERS = {1: "weight"}` keyed by `analysis_cam_id`.
- `analysis_failures`: UNIQUE(service, source_partition, source_offset); joined to vehicles only by Kafka offset.
- Image path mapping: detector `/data/vehicle/<rest>` + trigger_code → `vehicle/<trigger_code>/<rest>` (assumes
  service name == trigger_code); analyzer `/workspace/traffic/<rest>` → `<rest>`. `_safe_relative` rejects `..`.

### 4.4 Filesystem (`runtime/`, root-owned, gitignored, **no retention**)
```text
runtime/engines/{in,out}_gate1_lane{1,2}.engine, plate_detection.engine, plate_recognition_yolo26n.engine (plate_recognition.engine = old best224), *.trtexec.log, .tensorrt-version
runtime/vehicle/<site>/<YYYYMMDD local, utc_offset_hours>/<ns>_<src>_<objid>_<plate|None>_<trigger_code>.jpg   (full 2688x1520 frame)
runtime/weight_result/<YYYYMMDD local>/weight_<trigger_code>_<epoch float>.jpg             (LCD crop)
runtime/thung_hang/                                                                            (legacy cargo output)
config_ui/data/backups/<stamp>/...                                                             (last 30 applies)
```
No Redis/cache layer. Kafka is the only queue; topic partitioning: detector always writes partition 0.

---

## 5. API & Interface Contracts

### 5.1 Kafka topics

| Topic | Producer → Consumer | Payload |
|---|---|---|
| `VehicleDetection_Gate1` | detector (partition `output_partition`=0, `acks=all`, retries 5) → `weight_reader` (group `moda-weight-reader`), `results_recorder` | `events.build_vehicle_message` (below) |
| `AnalysisResult_Gate1` | `weight_reader` → `results_recorder`, external consumers | trigger payload + `analysis_cam_id=1`, `analysis_code="weight_gate1_<lane1|lane2>"`, `analysis_image` (container path), `analysis_output` (int or float) |
| `AnalysisFailure_Gate1` | `weight_reader` (DLQ) → `results_recorder` | `{service, source_topic, source_partition, source_offset, trigger_code, attempts, error (last 2000 chars), failed_at ISO}` |

Vehicle payload (legacy contract — keep keys/types):
```json
{"trigger_cam_id": 0, "analysis_cam_id": -1, "trigger_code": "in_gate1_lane1", "section_id": "<uuid4>",
 "analysis_code": "", "detected_object_id": "29H12345" | null, "trigger_image": "/data/vehicle/20261117/<file>.jpg",
 "trigger_extra_data": "", "analysis_image": "", "analysis_extra_data": "", "analysis_output": "",
 "timestamp": "2026-11-17T09:31:27.123Z", "frame_num": 1234, "times_in_video": 123.4,
 "raw_license_plate": "...", "best_match_lp": "..." , "score_matching": 0.93 | "", "search": true}
```
`detected_object_id` = best plate (None if no valid plate). `score_matching` is `""` when nothing matched.
`times_in_video = frame_num / video_fps` (config value, not measured).

### 5.2 CLIs
| Command | Contract |
|---|---|
| `python3 -m traffic_core --site <json> [--gpu N]` | exits non-zero on config error, pipeline error, source stall |
| `python3 docker/prepare_engines.py <site.json>` / `<model.onnx>` | build engines / print first ONNX input |
| `python3 -m post_analysis weight [--config config/post_analysis.json]` | runs until SIGTERM |
| `python -m moda_ui serve [--host --port]` | uvicorn, **workers=1** |
| `python -m moda_ui import [--force]` | files + `.env`/`.env.jetson` → DB |
| `python -m moda_ui create-admin <user> [--role viewer] [--password]` | min 8 chars |
| `python -m moda_ui generate` | print diffs + affected services, exit 1 on validation errors |
| `python -m moda_ui gen-key` / `record` | Fernet key / results recorder |
| `scripts/analysis-service.sh {start|stop|restart|status|logs|config} [env-file]` | analysis compose with preflight |

### 5.3 Config UI HTTP API (`api.py`, prefix `/api`; OpenAPI at `/api/docs`)
Auth: cookie `moda_session` = Fernet token `{uid, role}` (key derived `sha256(secret|"session")`), TTL
`MODA_UI_SESSION_HOURS` (12), `httponly`, `samesite=strict`. `User` = any logged-in, `Admin` = role admin.

| Method & path | Auth | Notes |
|---|---|---|
| POST `/auth/login` `{username,password}` / POST `/auth/logout` / GET `/auth/me` | – / – / User | 401 on bad creds |
| GET,POST `/users`; PUT `/users/{id}/password`; DELETE `/users/{id}` | Admin | `UserCreate` username 3–64, password ≥8 |
| GET,POST `/cameras`; PUT,DELETE `/cameras/{id}` | GET User, write Admin | `CameraIn{code ^[a-z0-9_]+$, name, purpose lane|weight, env_name ^[A-Z][A-Z0-9_]*$, rtsp_url?}`; URL masked on output; omit URL on update = keep |
| GET `/cameras/{id}/snapshot` | User | JPEG, headers `X-Frame-Width/Height`, stores resolution |
| GET `/cameras/{id}/live` | User | `multipart/x-mixed-replace; boundary=frame` MJPEG |
| POST `/cameras/{id}/test` | User | `{ok,width,height}` |
| GET,POST `/plate-lists`; GET,PUT `/plate-lists/{id}/plates`; GET `/plate-lists/{id}/export` (text); DELETE | read User, write Admin | |
| GET `/sites`, `/sites/{code}`; PUT `/sites/{code}`; PUT,DELETE `/sites/{code}/sources/{index}`; PUT `/sites/{code}/sources/{index}/zones/{capture|inference}` | read User, write Admin | `SiteIn`, `SourceIn{camera_id,trigger_code}`, `ZoneIn{points ≥3, non-negative}` |
| GET,PUT `/settings/system` | User / Admin | `SystemSettings` |
| GET,PUT `/analyzers/{analyzer}`; PUT `/analyzers/weight/lanes/{trigger_code}`; DELETE `/analyzers/{analyzer}/lanes/{trigger_code}` | User / Admin | only `weight` valid; `WeightLaneIn` crop must be non-empty |
| GET `/models/options` / `/models/file?path=` | User | scans `models/**/*.txt` (`[tracker]` or `[property]`+`gie-unique-id`), Triton dirs with `config.pbtxt`; file view restricted to `/home/jetson/models/*.txt` |
| GET `/apply/preview`; POST `/apply` `{note}`; GET `/apply/status` | User / Admin / User | 409 busy, 422 `{"detail":{"errors":[...]}}` |
| GET `/revisions`, `/revisions/{id}`; POST `/revisions/{id}/rollback` | User / Admin | |
| GET `/services`; GET `/services/{detector|analysis}/{service}/logs?tail=` | User | tail clamped 1..1000; 502 on docker error |
| GET `/api/results?lane&plate&date_from&date_to&unmatched&no_plate&page&page_size≤200`, `/api/results/stats`, `/api/results/lanes`, `/api/results/image?path&w`, `/api/results/{id}` | User | image served from `runtime/`, optional thumbnail width 32–1600 |
| GET `/{path}` | – | SPA fallback to `index.html` when `MODA_UI_STATIC_DIR` set |

Errors: FastAPI default `{"detail": ...}`; global handlers map `repository.NotFound` → 404 (`"Không tìm thấy ..."`),
`repository.Conflict` → 409, `security.SecretKeyError` → 500. Messages are Vietnamese. No other middleware (no CORS,
no CSRF token beyond `samesite=strict`).

Frontend routes (`frontend/src/App.tsx`): `/` Dashboard, `/results`, `/live`, `/cameras`, `/sites[/:code]`, `/weight`,
`/plates`, `/system`, `/revisions`; unauthenticated → LoginPage. Dev proxy `/api` → `localhost:8080` (Vite :5173).

---

## 6. Configuration, Build & Runtime

### 6.1 Environment variables (never commit/print real values; `.env*` are gitignored and partly root-owned)

| Var | File / consumer | Purpose |
|---|---|---|
| `MODA_IN_GATE1_LANE1_CAMERA_URL`, `MODA_IN_GATE1_LANE2_CAMERA_URL`, `MODA_OUT_GATE1_LANE1_CAMERA_URL`, `MODA_OUT_GATE1_LANE2_CAMERA_URL` | `.env` → `compose.yaml` → site JSON `camera_url_envs` | lane RTSP URLs (`rtsp://user:pass@host:port/...`) |
| `MODA_KAFKA_BOOTSTRAP_SERVERS` | `.env`, `.env.jetson`, `.env.ui` | Kafka bootstrap (required by every service; compose `:?` fails if unset) |
| `MODA_SOURCE_STALL_SECONDS` (60), `MODA_SOURCE_STARTUP_SECONDS` (120) | detector env (not wired in compose yet) | watchdog limits, must be > 0 |
| `MODA_RTSP_PROTOCOLS` (`tcp`) | detector env | rtspsrc `protocols`; `""` keeps GStreamer default |
| `MODA_ENGINE_CACHE_DIR` (`/cache/engines`) | `prepare_engines.py` | engine cache |
| `CUDA_VER` (12.6) | detector image | parser build |
| `COMPOSE_PROJECT_NAME=moda-analysis`, `MODA_ANALYSIS_BASE_IMAGE`, `MODA_ANALYSIS_IMAGE`, `MODA_TRITON_IMAGE` | `.env.jetson` | analysis images / project name |
| `MODA_TRITON_URL` (`http://triton:8000`) | `.env.jetson` → weight_reader | Triton base URL; model URL = `<url>/<weight.model>` |
| `MODA_WEIGHT_CAMERA_URL` | `.env.jetson` | weighbridge LCD RTSP |
| `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp` | compose.analysis.yaml; defaulted in `live.py`/`snapshot.py` | OpenCV RTSP over TCP |
| `MODA_PROJECT_DIR` | `.env.ui` (required) | absolute checkout path; mounted at the **same path** in the container |
| `MODA_CONFIG_SECRET` | `.env.ui` (required) | Fernet key: camera URL encryption + derived session key. Losing/changing it = re-enter all URLs and logs everyone out |
| `MODA_UI_BIND` (0.0.0.0), `MODA_UI_PORT` (8080), `MODA_UI_HEALTH_WAIT_SECONDS` (20), `MODA_UI_SESSION_HOURS` (12) | `.env.ui` / env | UI binding, apply health wait, session TTL |
| `MODA_CONFIG_DB` (`/data/moda_config.db`), `MODA_RESULTS_DB`, `MODA_UI_STATIC_DIR` | compose.ui / image | DB paths, built frontend |
| `MODA_DOCKER_BINARY`, `MODA_DETECTOR_COMPOSE` (`compose.yaml`), `MODA_DETECTOR_ENV` (`.env`), `MODA_ANALYSIS_COMPOSE` (`compose.analysis.yaml`), `MODA_ANALYSIS_ENV` (`.env.jetson`) | config_ui | compose targets |
| `MODA_LIVE_FPS` (15), `MODA_LIVE_WIDTH` (960), `MODA_LIVE_SUBSTREAM` (1) | config_ui | live view |
| `MODA_RESULTS_GROUP_ID`, `MODA_RESULTS_{VEHICLE,ANALYSIS,FAILURE}_TOPIC` | results_recorder | consumer group / topics |

Static config (JSON) keys: site — `name, camera_url_envs[], trigger_codes{src}, capture_zones{src}, inference_zones{src},
models{tracker, vehicle_detection, plate_detection, plate_recognition}, reference_plate_path, utc_offset_hours` (required) +
`vehicle_confidence (0.8), plate_character_confidence (0.8), plate_collection_seconds (4), video_fps (10),
output_topic, output_partition, publish_timeout_seconds (30), vehicle_image_root (/data/vehicle),
kafka_bootstrap_servers_env`. Current sites: confidences 0.7; lane1 sites 4 s/10 fps, lane2 sites 5 s/30 fps;
`in_gate1_lane1`/`out_gate1_lane1` use `standard.txt`, lane2 uses `lane2.txt` (in_gate1_lane2 uses `standard.txt`).
`post_analysis.json` — see `config/post_analysis.json` (worker_count 2, event_timeout 75 s, max_attempts 2,
max_seconds 60, confidence 0.5, drop_threshold 3000, max_drop_count 70, crops `[x_min, x_max, y_min, y_max]`).

### 6.2 Commands (run in repo root on the Jetson unless noted)

```bash
# Detector: one container per gate (in_gate1, out_gate1), each runs its 2 lanes as 2 pipelines in one
# process (first start builds TRT engines, several minutes). Switching from the old per-lane services:
# docker compose up -d --build --remove-orphans in_gate1 out_gate1
cp .env.example .env                                   # fill camera URLs + Kafka
docker compose up -d --build in_gate1 out_gate1
docker compose logs -f in_gate1
# Bare-metal (DeepStream 7.1 installed, parser + engines built manually)
python3 -m traffic_core --site config/sites/in_gate1_lane1.json --gpu 0

# Analysis
cp .env.jetson.example .env.jetson
chmod +x scripts/analysis-service.sh
./scripts/analysis-service.sh config && ./scripts/analysis-service.sh start
docker compose --env-file .env.jetson -f compose.analysis.yaml up -d --build triton weight_reader

# Config UI
cp .env.ui.example .env.ui                              # MODA_PROJECT_DIR=$(pwd), MODA_CONFIG_SECRET=<gen-key>
docker compose -f compose.ui.yaml --env-file .env.ui build
docker compose -f compose.ui.yaml --env-file .env.ui run --rm config_ui import
docker compose -f compose.ui.yaml --env-file .env.ui run --rm config_ui create-admin admin
docker compose -f compose.ui.yaml --env-file .env.ui up -d            # http://<jetson-ip>:8080

# Tests (stdlib unittest; pure modules only — pyds/GStreamer code is not unit-tested)
PYTHONPATH=. python3 -B -m unittest discover -s tests    # tests/ has no __init__.py; 36 tests pass on host python3.10
# UI tests need fastapi/pydantic/yaml: run them in the UI image (15 tests)
docker run --rm -v "$PWD":"$PWD" -w "$PWD" --entrypoint python3 moda-config-ui:latest \
  -m unittest discover -s config_ui/backend/tests -t config_ui/backend
# Generated files vs disk without writing anything (expect "0 file thay đổi")
docker compose -f compose.ui.yaml --env-file .env.ui run --rm --no-deps config_ui generate
# Frontend dev
cd config_ui/frontend && npm install && npm run dev                   # build: npm run build (tsc -b && vite build)
```
No migrations: `schema.sql` is `CREATE TABLE IF NOT EXISTS` executed at every start — schema changes to existing
tables need manual `ALTER TABLE` (add it to `db.init_schema`). Verification beyond unit tests = smoke test on the
Jetson with real RTSP, Kafka and Triton.

---

## 7. Coding Conventions & Critical Gotchas

### 7.1 Conventions
- `from __future__ import annotations`, type hints, frozen `@dataclass` configs, module-level `logger = logging.getLogger(__name__)`; logging configured only in CLIs (`%(asctime)s %(levelname)s %(name)s`).
- Config is loaded once and validated eagerly by pure loaders raising `SiteConfigError` / `AnalysisConfigError` (both `ValueError`). Credentials and endpoints come **only** from env vars whose *names* are in JSON (`*_env` keys).
- Adapters isolated: Kafka in `kafka.py`, OpenCV capture in `capture.py`, Ultralytics in `models.py`; domain logic (`plate.py`, `tracking.py`, `geometry.py`, `events.py`, `WeightAccumulator`) is pure and unit-tested.
- Heavy imports are lazy (`cv2` inside `process_event`, `kafka` inside factory functions, `ultralytics` inside `load_yolo`) so config modules/tests import without GPU stacks.
- config_ui: raw `sqlite3` with `repository.py` as the only SQL layer, Pydantic models in `schemas.py` mirroring runtime constraints, one connection per request (`get_db`), `db.transaction()` context manager, Vietnamese user-facing errors.
- Generated JSON formatting (`generator.dump_json`): polygons/short dicts on one line, `_number` keeps `30` not `30.0`. Files written with LF.
- `.gitattributes`: LF for `*.sh *.py *.yaml *.yml *.json Dockerfile*`; `*.onnx`, `*.pt` via Git LFS. Entry script is also `sed 's/\r$//'`-ed in the Dockerfile.

### 7.2 Invariants — do not break
1. **`traffic_core/config.py` and `post_analysis/config.py` must stay stdlib-only.** `config_ui/validation.py` imports them from `MODA_PROJECT_DIR` inside the python:3.11 UI container (no pyds/cv2/kafka/numpy). Any new import there breaks every Apply.
2. **Site identity is one string in four places:** site JSON stem == `sites.code` == `--site .../<name>.json` in the command of one gate service in `compose.yaml` (`validation.site_service_names`; a service *named* `<name>` still works as fallback) == `runtime/vehicle/<name>` volume, mounted at `/data/vehicle/<name>` in a multi-site container (`cli.load_sites` appends the site stem to `vehicle_image_root` when >1 `--site`; `results.vehicle_image_relpath` accepts both forms); `trigger_codes` values are also expected to equal it (`results.vehicle_image_relpath` builds `vehicle/<trigger_code>/...`). `weight.crops`/`analysis_codes` keys must equal trigger codes; `_lane()` requires `lane1`/`lane2` substrings.
3. **Every env var in a site's `camera_url_envs` must be passed in that service's `environment:` in `compose.yaml`** (validation enforces; runtime raises `Missing runtime environment variable`).
4. **ROI coordinate spaces differ:** lane zones are in streammux space **2688×1520** (`DeepStreamPipeline.IMAGE_WIDTH/HEIGHT`, mirrored in `config_ui/settings.py stream_width/height`); weight crops are in the weighbridge camera's native resolution and ordered **`[x_min, x_max, y_min, y_max]`** (not x1,y1,x2,y2). Changing streammux size invalidates all zones.
5. Zone tests use the **bottom-centre foot point** `((x1+x2)//2, y2)` — not the box centre.
6. Component IDs are coupled with infer files: pgie `gie-unique-id=1`, sgie `2` (`operate-on-gie-id=1`), tgie `3` (`operate-on-gie-id=2`, `operate-on-class-ids=0;1`); `SKIP_SECONDARY_COMPONENT_ID=9999` hides out-of-zone vehicles from sgie. Extraction accepts components 1 **and** 9999. Vehicle target class is **0** (`front` in `object_detection/labels.txt`); plate classes `{0,1}`.
7. The inference-zone probe must stay on the **tracker src pad** (before sgie); the main probe on the **tgie src pad**. RGBA capsfilter must precede pgie for `get_nvds_buf_surface`; on aarch64 `unmap_nvds_buf_surface` after copying.
8. `infer*.txt` must keep `model-engine-file=/cache/engines/<name>.engine` (prepare_engines refuses paths outside the cache), `custom-lib-path=/opt/moda/libnvdsinfer_custom_impl_Yolo.so`, and **no** `engine-create-func-name` / `force-implicit-batch-dim` (TensorRT 10 removed those APIs). Plate engines are shared by every pipeline (2 gate containers × 2 lanes) — writes go through tmp + `os.replace`; the entrypoint prepares engines for every `--site` sequentially.
9. Kafka message schema is a legacy contract consumed outside this repo: keep all keys of `build_vehicle_message`; analyzers may only add/overwrite `analysis_*` fields (`build_analysis_result`). `analysis_cam_id=1` ⇔ weight (`results.ANALYZERS`).
10. Offset commit ordering in `runtime.py`: output (or DLQ) must be acknowledged **before** `tracker.complete()`; commit only contiguous offsets. Never enable auto-commit.
11. Worker processes use `multiprocessing` **spawn** context: `handler`, `handler_config` (frozen dataclasses) and events must stay picklable; workers must poll `stop_event` and the parent enforces the deadline with `terminate()`.
12. `config_ui` runs **one uvicorn worker**: `applier._lock` and `applier.status` are in-process globals.
13. config_ui container must mount the checkout at the **same absolute path** as the host (`${MODA_PROJECT_DIR}:${MODA_PROJECT_DIR}`) because compose relative volumes are resolved by the host daemon.
14. Once config_ui is used, `config/*`, `.env`, `.env.jetson` are **regenerated from SQLite** — hand edits are overwritten on the next Apply. Edit through the UI/DB, or re-run `import --force`.
15. Values written to `.env` files double `$` (`envfile._format_value`) for compose interpolation; readers must use `env_values_for_runtime` to compare.
16. Watchdog `stall_seconds` (60) must exceed `publish_timeout_seconds` (30): `publish()` blocks the GStreamer probe thread, so a slow broker freezes frame processing.
17. **No hard-coded time zone.** `utc_offset_hours` (int, −23..23) is **required** in every site JSON (`traffic_core.config.read_utc_offset_hours` → `VehicleDetection.local_timezone`) and in `post_analysis.json` (same rule in `post_analysis.config._load_common`); there is no code default. Its single source is `system_settings.utc_offset_hours` (UI page "Hệ thống"); `generator.build_files` writes it into all site files and `post_analysis.json`, so changing it recreates every deployed service. Event `timestamp` stays UTC.

### 7.3 Known gotchas & technical debt
- **One process per gate (since 2026-11-19).** `in_gate1`/`out_gate1` each run 2 independent pipelines (own pgie model, tracker, zones, watchdog, Kafka producer) on one GLib loop (`DeepStreamPipeline.run_pipelines`). Saves one CUDA context + TensorRT/DeepStream/Python runtime per lane; accuracy is unchanged (lanes still use 4 different vehicle ONNX weights, so pgie is not batched across lanes). Trade-offs: any EOS/ERROR/stall in one lane quits the loop and Docker restarts both lanes of that gate; both lanes share one GIL (probe work + synchronous Kafka publish). Log lines are prefixed with the site `name`.
- **Detector Kafka publish is synchronous inside the pad probe** (`future.get(timeout=30)`): a Kafka outage stalls the whole pipeline per pending track and can trip the watchdog → container restart → in-memory pending events lost (`_flush_pending_events` tries once at shutdown).
- Tracks unseen for 600 s are deleted **with their pending event** — a publish that keeps failing is dropped silently.
- Image file name embeds `plate_number`, literally `None` when no plate.
- Time zone support is whole hours only (no half-hour zones, no DST).
- `times_in_video` uses configured `video_fps`, not real stream FPS; lane1 sites say 10 fps, lane2 30 fps.
- Synthetic re-entry IDs start at 90000 and are per-process counters (reset on restart); NvDCF IDs are 64-bit so no collision in practice.
- `in_gate1_lane2` uses `object_detection/infer.txt` (`in_lane2.onnx`, `batch-size=3`) — not an `infer_in_lane2.txt`; pipeline overrides pgie batch to `len(camera_urls)` (=1) at runtime; engine built with opt=3/max=4.
- `object_detection/labels.txt` has 5 labels (front, back, car, other, side) but `num-detected-classes=4`.
- `models/triton/tram_can/config.pbtxt` is **empty** on purpose: Triton auto-generates ONNX config. Do not delete the file (preflight and `/models/options` check its existence) and do not fill it without matching I/O names.
- `weight_reader` reloads the YOLO/Triton client and reopens RTSP **per event** (spawned process) → several seconds of startup latency inside the 60 s window; each worker also creates its own Kafka producer.
- Routing key is the lane (`lane1`/`lane2`), so `in_gate1_laneX` and `out_gate1_laneX` supersede each other (same scale ROI). A superseded read keeps its stable value (>= 30 identical readings) and publishes it; otherwise it goes to the DLQ, never silently dropped.
- Weight values > 100 000 are divided by 10 (legacy, undocumented business rule; can return float). A reading of 0 wins only if nothing else was read.
- `weight_reader` consumer uses `auto_offset_reset="latest"`: with no committed offset for the group, events produced while it was down are skipped. `results_recorder` uses `earliest`.
- `supersede_same_key=True` keeps polling even at worker capacity, so `pending` can grow without bound if events arrive faster than they finish.
- `_configure_tracker` only maps `tracker-width`, `tracker-height`, `enable-past-frame`, `ll-lib-file`, `ll-config-file` from `config_tracker.txt`; other keys (e.g. `enable-batch-process`, `gpu-id`) are ignored. Missing file → `Invalid tracker configuration` (this is why the old JP5 containers `moda-*_gate1_*` in `~/hung_lm/moda` exited).
- `config/` is both baked into the detector image (`COPY config`) and mounted RO over `/app/config`; the mount wins. `models/` is excluded from the build context except 4 parser sources (`.dockerignore`).
- `Dockerfile` downloads `pyds-1.2.0-cp310` from GitHub at build time (needs internet); the repo-root `pyds-1.1.8` wheel is JP5 and unused. `deepstream:7.1-triton-multiarch` runs `user_additional_install.sh` for missing GStreamer plugins.
- `config_ui` mounts `/var/run/docker.sock` (= root on the host). Bind `MODA_UI_BIND` to the LAN IP. No rate limiting on login.
- First Apply after `import` rewrites every file (explicit defaults, deduped plates, normalized env) and recreates every deployed service — do it when no truck is passing. Only services already present in `docker compose ps -a` are recreated; others only get files. Triton is never recreated by the UI.
- Compose project names: analysis = `moda-analysis` (`COMPOSE_PROJECT_NAME`), UI = `moda-config-ui`; detector project name derives from the directory (or `COMPOSE_PROJECT_NAME` in `.env`) — renaming the checkout makes the UI see detectors as "not created". The `moda-kafka` broker belongs to the detector project (`compose.yaml`); `docker compose down` of that project stops Kafka too. The JP5 `moda-*` containers belong to another compose project.
- `runtime/` has no retention policy (full 2688×1520 JPEG per vehicle, ~1.5–2 MB each). Monitor disk.
- Uncommitted work at time of writing: watchdog (`traffic_core/watchdog.py`, `tests/test_watchdog.py`, wiring), `utc_offset_hours` change, land→lane rename (`git mv` of site/model files), deletion of `*.bak*` and JP5 `.so`, updated spec/README/AGENTS.md.
- History-only data still says `land`: `config_ui/data/backups/*` (file backups of earlier Applies), `config_revisions.files_diff`/`log`, `analysis_failures.error`, the `runtime/thung_hang/` legacy folder, and old messages retained in the Kafka topics. Revision `snapshot_json` (used by rollback) was migrated.
- Unused file left in place: `models/config_dsanalytics.txt`.
- The cargo analyzer ("thùng hàng") was removed in `e56ae7e`, but the schema still allows `purpose='cargo'` / `analyzer='cargo'` (cleaned by `db.remove_cargo`).
- README claim "Healthcheck only confirms Triton HTTP ready" holds: model load and inference correctness are only verified by on-device smoke tests.
- **Character model (since 2026-11-18): YOLO26n** `models/plate_recognition/yolo26n_doc_bien_ds.onnx`. Ultralytics exports a single `output0 [1, 4+nc, 8400]`; DeepStream-Yolo `NvDsInferParseYolo` needs `boxes [N,4] (xc,yc,w,h px) / scores [N,1] / classes [N,1 float]` in that order, so a DeepStream head (Transpose → Slice → ReduceMax/ArgMax → Cast) was appended to the graph with `scripts/ultralytics_onnx_to_deepstream.py <in.onnx> <out.onnx>` (verified identical to `output0`). Any new Ultralytics export must get the same head before use. Its attention `Reshape` nodes hard-code batch 1, so `batch-size=1` in `plate_recognition/infer.txt` (export with `dynamic=True` to allow more). Labels: `labels_yolo26n.txt` (31 lines, order = training `data.yaml`); `num-detected-classes` must equal its line count. Test-set plate accuracy (743 plates, DeepStream-equivalent pre/post-processing incl. `plate.merge_box`): new 85.6 % vs old `best224` 38.9 %; `symmetric-padding=1` did not help. Rollback: restore `onnx-file=best224.onnx`, `labelfile-path=labels.txt`, `model-engine-file=/cache/engines/plate_recognition.engine`, `batch-size=2`, `num-detected-classes=36`, then recreate the 2 gate detectors.

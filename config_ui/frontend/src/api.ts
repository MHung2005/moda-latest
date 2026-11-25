// Typed client for the FastAPI backend (/api).

export type Point = [number, number];
export type Role = "admin" | "viewer";

export interface User {
  id: number;
  username: string;
  role: Role;
  created_at?: string;
}

export interface Camera {
  id: number;
  code: string;
  name: string;
  purpose: "lane" | "weight";
  env_name: string;
  rtsp_url_masked: string;
  frame_width: number | null;
  frame_height: number | null;
  updated_at: string;
}

export interface CameraInput {
  code: string;
  name: string;
  purpose: Camera["purpose"];
  env_name: string;
  rtsp_url?: string | null;
}

export interface PlateList {
  id: number;
  name: string;
  file_name: string;
  plate_count: number;
}

export interface SiteSource {
  source_index: number;
  camera_id: number;
  trigger_code: string;
  capture_zone: Point[];
  inference_zone: Point[];
}

export interface SiteFields {
  name: string;
  vehicle_confidence: number;
  plate_character_confidence: number;
  plate_collection_seconds: number;
  video_fps: number;
  output_topic: string;
  output_partition: number;
  publish_timeout_seconds: number;
  model_tracker: string;
  model_vehicle_detection: string;
  model_plate_detection: string;
  model_plate_recognition: string;
  reference_plate_list_id: number;
}

export interface Site extends SiteFields {
  id: number;
  code: string;
  vehicle_image_root: string;
  updated_at: string;
  sources: SiteSource[];
}

export interface SystemSettings {
  kafka_bootstrap_servers: string;
  triton_url: string;
  input_topic: string;
  output_topic: string;
  dead_letter_topic: string;
  publish_timeout_seconds: number;
  utc_offset_hours: number;
}

export interface AnalyzerLane {
  analyzer: "weight";
  trigger_code: string;
  analysis_code: string;
  crop_x_min: number | null;
  crop_x_max: number | null;
  crop_y_min: number | null;
  crop_y_max: number | null;
  camera_id: number | null;
}

export interface Analyzer {
  settings: Record<string, number | string>;
  lanes: AnalyzerLane[];
}

export interface FileDiff {
  path: string;
  created: boolean;
  diff: string;
}

export interface Preview {
  files: FileDiff[];
  errors: string[];
  services: Record<string, string[]>;
  pending: number;
}

export interface ApplyResult {
  ok: boolean;
  revision_id: number | null;
  services: string[];
  log: string[];
  errors?: string[];
}

export interface ApplyStatus {
  running: boolean;
  step: string;
  log: string[];
  ok: boolean | null;
  revision_id: number | null;
}

export interface Revision {
  id: number;
  created_at: string;
  note: string;
  services: string[];
  status: "applied" | "failed" | "rolled_back";
  username: string | null;
  files_diff?: string;
  log?: string;
}

export interface ServiceState {
  project: "detector" | "analysis";
  service: string;
  managed: boolean;
  deployed: boolean;
  state: string;
  status: string;
  health: string;
  container: string;
  error: string | null;
}

export interface ModelOptions {
  tracker: string[];
  inference: string[];
  triton: string[];
}

export interface AnalysisOutput {
  output: string | null;
  analysis_code: string | null;
  image_path: string | null;
  received_at: string;
}

export interface VehicleResult {
  id: number;
  section_id: string;
  trigger_code: string;
  trigger_cam_id: number | null;
  plate: string | null;
  raw_plate: string | null;
  best_match: string | null;
  match_score: number | null;
  requires_search: boolean;
  in_reference: boolean;
  event_time: string;
  frame_num: number | null;
  image_path: string | null;
  received_at: string;
  weight: AnalysisOutput | null;
  failures: { analyzer: string; error: string; failed_at: string | null }[];
}

export interface ResultPage {
  total: number;
  page: number;
  page_size: number;
  items: VehicleResult[];
}

export interface ResultStats {
  total: number;
  no_plate: number;
  in_reference: number;
  out_of_reference: number;
  last_event: string | null;
  lanes: { lane: string; count: number }[];
}

export interface ResultFilter {
  lane?: string;
  plate?: string;
  date_from?: string;
  date_to?: string;
  unmatched?: boolean;
  no_plate?: boolean;
  page?: number;
  page_size?: number;
}

export interface PlateCheckSummary {
  trigger_code?: string;
  plate?: string | null;
  raw_plate?: string | null;
  best_match?: string | null;
  match_score?: number | null;
  requires_search?: boolean;
  capture_frame?: number | null;
  captured_at?: string | number | null;
  image_path?: string | null;
  plate_votes?: Record<string, number>;
}

export interface PlateCheckVehicle {
  section_id: string;
  summary: PlateCheckSummary | null;
  capture_image_path: string | null;
  frame_count: number;
  valid_frame_count: number;
  first_ts: number | string | null;
  last_ts: number | string | null;
  updated_at: number;
}

export interface PlateCheckFrame {
  frame_num: number | null;
  ts: number | string | null;
  plate_label: string | null;
  plate_confidences: number[] | null;
  valid_plate: boolean;
  in_capture_zone: boolean;
  vehicle_confidence: number | null;
  box: number[] | null;
  image_path: string | null;
}

export interface PlateCheckDetail {
  lane: string;
  section_id: string;
  summary: PlateCheckSummary | null;
  capture_image_path: string | null;
  frames: PlateCheckFrame[];
}

function query(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export class ApiError extends Error {
  status: number;
  details: string[];
  constructor(status: number, message: string, details: string[] = []) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

function describe(detail: unknown): { message: string; details: string[] } {
  if (typeof detail === "string") return { message: detail, details: [] };
  if (Array.isArray(detail)) {
    // FastAPI/pydantic validation errors
    const details = detail.map((item: { loc?: unknown[]; msg?: string }) => {
      const field = (item.loc ?? []).filter((part) => part !== "body").join(".");
      return field ? `${field}: ${item.msg}` : String(item.msg);
    });
    return { message: details[0] ?? "Dữ liệu không hợp lệ", details };
  }
  if (detail && typeof detail === "object" && "errors" in detail) {
    const errors = (detail as { errors: string[] }).errors;
    return { message: "Cấu hình không hợp lệ", details: errors };
  }
  return { message: "Lỗi không xác định", details: [] };
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const type = response.headers.get("content-type") ?? "";
  const payload = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const { message, details } = describe(typeof payload === "string" ? payload : payload.detail);
    if (response.status === 401 && !path.startsWith("/auth/")) {
      window.dispatchEvent(new Event("moda:unauthorized"));
    }
    throw new ApiError(response.status, message, details);
  }
  return payload as T;
}

const get = <T,>(path: string) => request<T>("GET", path);
const put = <T,>(path: string, body: unknown) => request<T>("PUT", path, body);
const post = <T,>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});
const del = <T,>(path: string) => request<T>("DELETE", path);

export const api = {
  login: (username: string, password: string) => post<User>("/auth/login", { username, password }),
  logout: () => post("/auth/logout"),
  me: () => get<User>("/auth/me"),

  users: () => get<User[]>("/users"),
  createUser: (data: { username: string; password: string; role: Role }) => post("/users", data),
  changePassword: (id: number, password: string) => put(`/users/${id}/password`, { password }),
  deleteUser: (id: number) => del(`/users/${id}`),

  cameras: () => get<Camera[]>("/cameras"),
  createCamera: (data: CameraInput) => post<Camera>("/cameras", data),
  updateCamera: (id: number, data: CameraInput) => put<Camera>(`/cameras/${id}`, data),
  deleteCamera: (id: number) => del(`/cameras/${id}`),
  testCamera: (id: number) => post<{ ok: boolean; width: number; height: number }>(`/cameras/${id}/test`),
  snapshotUrl: (id: number, nonce: number) => `/api/cameras/${id}/snapshot?t=${nonce}`,
  liveUrl: (id: number, nonce: number) => `/api/cameras/${id}/live?t=${nonce}`,

  plateLists: () => get<PlateList[]>("/plate-lists"),
  createPlateList: (name: string) => post<{ id: number }>("/plate-lists", { name }),
  plates: (id: number) => get<{ plates: string[] }>(`/plate-lists/${id}/plates`),
  replacePlates: (id: number, plates: string[]) => put<{ count: number }>(`/plate-lists/${id}/plates`, { plates }),
  deletePlateList: (id: number) => del(`/plate-lists/${id}`),
  exportPlatesUrl: (id: number) => `/api/plate-lists/${id}/export`,

  sites: () => get<Site[]>("/sites"),
  updateSite: (code: string, data: SiteFields) => put<Site>(`/sites/${code}`, data),
  upsertSource: (code: string, index: number, data: { camera_id: number; trigger_code: string }) =>
    put<Site>(`/sites/${code}/sources/${index}`, data),
  deleteSource: (code: string, index: number) => del<Site>(`/sites/${code}/sources/${index}`),
  setZone: (code: string, index: number, kind: "capture" | "inference", points: Point[]) =>
    put<Site>(`/sites/${code}/sources/${index}/zones/${kind}`, { points }),

  system: () => get<SystemSettings>("/settings/system"),
  updateSystem: (data: SystemSettings) => put<SystemSettings>("/settings/system", data),

  analyzer: (name: "weight") => get<Analyzer>(`/analyzers/${name}`),
  updateAnalyzer: (name: "weight", data: Record<string, unknown>) => put(`/analyzers/${name}`, data),
  upsertWeightLane: (trigger: string, data: Record<string, unknown>) => put(`/analyzers/weight/lanes/${trigger}`, data),
  deleteLane: (name: "weight", trigger: string) => del(`/analyzers/${name}/lanes/${trigger}`),

  modelOptions: () => get<ModelOptions>("/models/options"),
  modelFile: (path: string) => get<string>(`/models/file?path=${encodeURIComponent(path)}`),

  preview: () => get<Preview>("/apply/preview"),
  apply: (note: string) => post<ApplyResult>("/apply", { note }),
  applyStatus: () => get<ApplyStatus>("/apply/status"),
  revisions: () => get<Revision[]>("/revisions"),
  revision: (id: number) => get<Revision>(`/revisions/${id}`),
  rollback: (id: number) => post<ApplyResult>(`/revisions/${id}/rollback`),

  results: (filter: ResultFilter) => get<ResultPage>(`/results${query(filter)}`),
  resultStats: (filter: Pick<ResultFilter, "date_from" | "date_to">) =>
    get<ResultStats>(`/results/stats${query(filter)}`),
  resultLanes: () => get<string[]>("/results/lanes"),
  resultImageUrl: (path: string, width?: number) =>
    `/api/results/image${query({ path, w: width })}`,

  plateCheck: () => get<{ lanes: { lane: string; vehicles: PlateCheckVehicle[] }[] }>("/plate-check"),
  plateCheckVehicle: (lane: string, sectionId: string) =>
    get<PlateCheckDetail>(`/plate-check/${encodeURIComponent(lane)}/${encodeURIComponent(sectionId)}`),

  services: () => get<ServiceState[]>("/services"),
  serviceLogs: (project: string, service: string) => get<string>(`/services/${project}/${service}/logs?tail=200`),
};

/** Shapes of the Analytics API's JSON responses (see the README's API section). */

export type DatasetStatus = "RECEIVED" | "PROCESSING" | "COMPLETED" | "FAILED" | "DUPLICATE";
export type AnomalyMethod = "zscore" | "term_jump" | "duplicate_id" | "isolation_forest";
export type JumpPattern = "spike" | "sustained" | "latest";

export interface DatasetSummary {
  dataset_id: string;
  filename: string | null;
  uploaded_at: string | null;
  status: DatasetStatus;
  total_rows: number;
  valid_rows: number;
  clean_rows: number;
  anomaly_count: number;
  duplicate_of: string | null;
  error_message: string | null;
}

export interface DatasetsPage {
  datasets: DatasetSummary[];
  next_cursor: string | null;
}

export interface DatasetDetail extends DatasetSummary {
  validation: {
    total_rows: number;
    valid_rows: number;
    invalid_rows: number;
    row_errors: string[];
    error_message: string | null;
  };
  cleaning: { clean_rows: number; duplicates_removed: number; processed_key: string | null };
  anomalies: { count: number; stored: number; ml_skipped: string | null };
  quarantine_key: string | null;
  file_hash: string | null;
}

export interface ScoreSummary {
  count: number;
  mean: number;
  median: number;
  std: number;
  min: number;
  max: number;
  pass_rate: number;
}

export interface StudentAverage {
  student_id: string;
  student_name: string;
  average: number;
}

export interface SubjectTermStats extends ScoreSummary {
  subject: string;
  term: string;
}

export interface StatsResponse {
  dataset_id: string;
  status: DatasetStatus;
  overall: ScoreSummary | null; // null while the dataset is unfinished or failed
  student_count: number | null;
  top_students: StudentAverage[];
  bottom_students: StudentAverage[];
  class_averages: Record<string, number>;
  by_subject_term: SubjectTermStats[];
}

export interface Anomaly {
  rank: number;
  method: AnomalyMethod;
  student_id: string;
  student_name: string | null;
  subject: string | null;
  term: string | null;
  score: number | null;
  pattern: JumpPattern | null;
  severity: number;
  details: string;
}

export interface AnomaliesResponse {
  dataset_id: string;
  status: DatasetStatus;
  anomaly_count: number;
  anomalies_stored: number;
  ml_skipped: string | null;
  items: Anomaly[];
  next_cursor: string | null;
}

/** A pre-signed S3 form: POST `fields`, then the file as the LAST field, to `url`. */
export interface UploadForm {
  dataset_id: string;
  key: string;
  upload: { method: "POST"; url: string; fields: Record<string, string> };
  max_bytes: number;
  expires_in: number;
}

export interface BackendJob {
  github_job_id: number;
  name: string;
  status: string;
  conclusion: string | null;
  html_url: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface BackendRun {
  github_run_id: number;
  workflow_name: string;
  status: string;
  conclusion: string | null;
  html_url: string;
  attempt: number;
  created_at: string;
  updated_at: string;
  jobs: BackendJob[];
}

export interface BackendResponse {
  runs: BackendRun[];
}

import { fetchRuns, triggerCI, rerunCI } from './api-client';
import { BackendJob, BackendRun } from './types';

type RunStatus = 'RUNNABLE' | 'RUNNING' | 'SCHEDULED' | 'COMPLETED';
type Category = 'SUCCESS' | 'INFO' | 'WARNING' | 'ERROR';
type ResponseCode = 'OK' | 'ERROR';

interface ChangeData {
  changeNumber: number;
  patchsetNumber: number;
  patchsetSha: string;
  repo: string;
}

interface CheckResult {
  externalId?: string;
  category: Category;
  summary: string;
  message?: string;
  tags?: { name: string; tooltip?: string; color?: string }[];
  links?: { url: string; primary: boolean; icon: string; tooltip?: string }[];
  actions?: Action[];
}

interface Action {
  name: string;
  tooltip?: string;
  primary?: boolean;
  summary?: boolean;
  disabled?: boolean;
  callback: (
    change: number,
    patchset: number,
    attempt: number | undefined,
    externalId: string | undefined,
    checkName: string | undefined,
    actionName: string,
  ) => Promise<{ message?: string; shouldReload?: boolean }>;
}

interface CheckRun {
  checkName: string;
  externalId?: string;
  status: RunStatus;
  statusDescription?: string;
  checkLink?: string;
  labelName?: string;
  attempt?: number;
  scheduledTimestamp?: Date;
  startedTimestamp?: Date;
  finishedTimestamp?: Date;
  results?: CheckResult[];
  actions?: Action[];
}

interface FetchResponse {
  responseCode: ResponseCode;
  errorMessage?: string;
  summaryMessage?: string;
  actions?: Action[];
  runs?: CheckRun[];
}

function parseDate(s: string | null): Date | undefined {
  if (!s) return undefined;
  const d = new Date(s);
  return isNaN(d.getTime()) ? undefined : d;
}

function mapJobStatus(job: BackendJob): RunStatus {
  switch (job.status) {
    case 'queued':
      return 'SCHEDULED';
    case 'in_progress':
      return 'RUNNING';
    case 'completed':
      return 'COMPLETED';
    default:
      return 'RUNNING';
  }
}

function mapConclusion(conclusion: string | null): Category {
  switch (conclusion) {
    case 'success':
      return 'SUCCESS';
    case 'failure':
      return 'ERROR';
    case 'cancelled':
      return 'WARNING';
    case 'skipped':
      return 'INFO';
    default:
      return 'INFO';
  }
}

function getStatusDescription(job: BackendJob): string {
  if (job.status === 'queued') return 'Queued';
  if (job.status === 'in_progress') return 'Running';
  if (job.status === 'completed' && job.conclusion) {
    return job.conclusion.charAt(0).toUpperCase() + job.conclusion.slice(1);
  }
  return job.status;
}

function getJobTags(
  jobName: string,
): { name: string; color: string }[] {
  const tags: { name: string; color: string }[] = [];
  const lower = jobName.toLowerCase();
  if (lower.includes('unittest') || lower.includes('build')) {
    tags.push({ name: 'BUILD', color: 'purple' });
  }
  if (lower.includes('test') && !lower.includes('unittest')) {
    tags.push({ name: 'TEST', color: 'cyan' });
  }
  if (lower.includes('lint') || lower.includes('format') || lower.includes('scan')) {
    tags.push({ name: 'LINT', color: 'brown' });
  }
  if (lower.includes('rdma')) {
    tags.push({ name: 'RDMA', color: 'pink' });
  }
  if (lower.includes('vm') || lower.includes('qemu')) {
    tags.push({ name: 'VM', color: 'yellow' });
  }
  return tags;
}

function makeRerunAction(
  changeNumber: number,
  patchsetNumber: number,
): Action {
  return {
    name: 'Rerun Failed',
    tooltip: 'Rerun failed jobs in this CI run',
    callback: async () => {
      try {
        await rerunCI(changeNumber, patchsetNumber);
        return { message: 'Rerun triggered', shouldReload: true };
      } catch (e) {
        return { message: `Rerun failed: ${e}` };
      }
    },
  };
}

function buildSummary(runs: BackendRun[]): string | undefined {
  const allJobs = runs.flatMap((r) => r.jobs);
  if (allJobs.length === 0) return undefined;
  const allCompleted = allJobs.every((j) => j.status === 'completed');
  if (!allCompleted) {
    const running = allJobs.filter((j) => j.status === 'in_progress').length;
    const queued = allJobs.filter((j) => j.status === 'queued').length;
    const done = allJobs.filter((j) => j.status === 'completed').length;
    return `**CI Status:** ${done}/${allJobs.length} jobs completed, ${running} running, ${queued} queued`;
  }
  const passed = allJobs.filter((j) => j.conclusion === 'success').length;
  const failed = allJobs.filter((j) => j.conclusion === 'failure').length;
  const url = runs[0]?.html_url;
  let msg = `**CI Results:** ${passed}/${allJobs.length} jobs passed`;
  if (failed > 0) msg += `, ${failed} failed`;
  if (url) msg += `. [View full run](${url})`;
  return msg;
}

function mapJob(
  job: BackendJob,
  run: BackendRun,
  changeNumber: number,
  patchsetNumber: number,
): CheckRun {
  const actions: Action[] = [];

  // Add rerun action if this run has any failures
  const hasFailures = run.jobs.some((j) => j.conclusion === 'failure');
  if (run.status === 'completed' && hasFailures) {
    actions.push(makeRerunAction(changeNumber, patchsetNumber));
  }

  const results: CheckResult[] = [];
  if (job.status === 'completed') {
    results.push({
      externalId: `github-job-result-${job.github_job_id}`,
      category: mapConclusion(job.conclusion),
      summary: `${job.name}: ${job.conclusion ?? 'unknown'}`,
      links: job.html_url
        ? [
            {
              url: job.html_url,
              primary: true,
              icon: 'external',
              tooltip: 'View in GitHub Actions',
            },
          ]
        : [],
      tags: getJobTags(job.name),
    });
  }

  return {
    checkName: job.name,
    externalId: `github-job-${job.github_job_id}`,
    status: mapJobStatus(job),
    statusDescription: getStatusDescription(job),
    checkLink: job.html_url || undefined,
    labelName: 'Verified',
    attempt: run.attempt,
    scheduledTimestamp: parseDate(run.created_at),
    startedTimestamp: parseDate(job.started_at),
    finishedTimestamp: parseDate(job.completed_at),
    results,
    actions,
  };
}

export class SpdkChecksProvider {
  private plugin: any;

  constructor(plugin: any) {
    this.plugin = plugin;
  }

  async fetch(changeData: ChangeData): Promise<FetchResponse> {
    try {
      const data = await fetchRuns(
        changeData.changeNumber,
        changeData.patchsetNumber,
      );

      if (!data.runs || data.runs.length === 0) {
        // No CI runs yet — show a single RUNNABLE entry with "Run CI" action
        return {
          responseCode: 'OK',
          runs: [
            {
              checkName: 'SPDK CI',
              status: 'RUNNABLE',
              statusDescription: 'CI not yet triggered',
              labelName: 'Verified',
              actions: [
                {
                  name: 'Run CI',
                  tooltip: 'Trigger CI for this patchset',
                  primary: true,
                  summary: true,
                  callback: async () => {
                    try {
                      await triggerCI(
                        changeData.changeNumber,
                        changeData.patchsetNumber,
                      );
                      return {
                        message: 'CI triggered',
                        shouldReload: true,
                      };
                    } catch (e) {
                      return { message: `Trigger failed: ${e}` };
                    }
                  },
                },
              ],
            },
          ],
        };
      }

      const checkRuns = data.runs.flatMap((run) =>
        run.jobs.map((job) =>
          mapJob(job, run, changeData.changeNumber, changeData.patchsetNumber),
        ),
      );

      const topLevelActions: Action[] = [];
      const allCompleted = data.runs.every((r) => r.status === 'completed');
      if (allCompleted) {
        topLevelActions.push({
          name: 'Run CI',
          tooltip: 'Trigger a new CI run for this patchset',
          primary: true,
          summary: true,
          callback: async () => {
            try {
              await triggerCI(
                changeData.changeNumber,
                changeData.patchsetNumber,
              );
              return { message: 'CI triggered', shouldReload: true };
            } catch (e) {
              return { message: `Trigger failed: ${e}` };
            }
          },
        });
      }

      return {
        responseCode: 'OK',
        summaryMessage: buildSummary(data.runs),
        actions: topLevelActions,
        runs: checkRuns,
      };
    } catch (e) {
      const message =
        e instanceof DOMException && e.name === 'AbortError'
          ? 'Backend request timed out'
          : `Failed to fetch CI status: ${e}`;
      return {
        responseCode: 'ERROR',
        errorMessage: message,
      };
    }
  }
}

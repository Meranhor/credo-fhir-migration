import { onMounted, ref, type Ref } from "vue";

export interface PatientSummary {
  id: number;
  fhir_id: string;
  name: string;
  gender: string;
  birth_date: string | null;
  observation_count: number;
  last_observation_at: string | null;
}

export interface Observation {
  id: number;
  fhir_id: string;
  status: string;
  category: string;
  code: string;
  display: string;
  value: string;
  value_type: "quantity" | "concept" | "string" | "components" | "none";
  effective_at: string | null;
  effective_date_only: boolean;
}

export interface PatientDetail {
  id: number;
  fhir_id: string;
  name: string;
  gender: string;
  birth_date: string | null;
  observations: Observation[];
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(response.status === 404 ? "Not found." : `Server error (HTTP ${response.status}).`);
  }
  return (await response.json()) as T;
}

export const getPatients = () => getJson<PatientSummary[]>("/api/patients/");
export const getPatient = (id: number) => getJson<PatientDetail>(`/api/patients/${id}/`);

/** Loading / error / data state for one request, with a retry. */
export function useRequest<T>(load: () => Promise<T>) {
  const data: Ref<T | null> = ref(null);
  const error = ref<string | null>(null);
  const loading = ref(true);

  async function run() {
    loading.value = true;
    error.value = null;
    try {
      data.value = await load();
    } catch (e) {
      error.value = e instanceof Error ? e.message : "Unexpected error.";
    } finally {
      loading.value = false;
    }
  }

  onMounted(run);
  return { data, error, loading, retry: run };
}

/** A day-only value is stored at 00:00 UTC: format it in UTC so it never shifts a day. */
export function formatWhen(value: string | null, dateOnly = false): string {
  if (!value) return "—";
  const date = new Date(value);
  return dateOnly
    ? date.toLocaleDateString(undefined, { timeZone: "UTC" })
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

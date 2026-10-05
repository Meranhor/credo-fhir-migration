<script setup lang="ts">
import { computed, ref } from "vue";
import { formatWhen, getPatients, useRequest } from "./api";

defineEmits<{ select: [id: number] }>();

const { data: patients, error, loading, retry } = useRequest(getPatients);

// Client-side filtering of the already loaded list: fine for a few thousand rows.
// At 50k patients this belongs on the server, with pagination.
const search = ref("");
const withObservationsOnly = ref(false);

const visible = computed(() => {
  const prefix = search.value.trim().toLowerCase();
  return (patients.value ?? []).filter((patient) => {
    if (withObservationsOnly.value && patient.observation_count === 0) return false;
    if (!prefix) return true;
    const name = patient.name.toLowerCase();
    // "sha" matches "Sharma"; "do" matches "Jane Doe"; "jane d" matches "Jane Doe".
    return name.startsWith(prefix) || name.split(/\s+/).some((word) => word.startsWith(prefix));
  });
});
</script>

<template>
  <section>
    <h1>Patients</h1>
    <p v-if="loading">Loading…</p>
    <p v-else-if="error" role="alert">
      Could not load patients: {{ error }} <button @click="retry">Retry</button>
    </p>
    <p v-else-if="!patients?.length">No patients yet. Run <code>make import</code> first.</p>
    <template v-else>
      <p>
        <label>Name starts with <input v-model="search" type="search" /></label>
        <label><input v-model="withObservationsOnly" type="checkbox" /> With observations only</label>
      </p>
      <p>{{ visible.length }} of {{ patients.length }} patients</p>
      <table v-if="visible.length">
        <thead>
          <tr>
            <th>Name</th>
            <th>Gender</th>
            <th>Birth date</th>
            <th>Observations</th>
            <th>Last observation</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="patient in visible" :key="patient.id">
            <td>
              <button class="link" @click="$emit('select', patient.id)">
                {{ patient.name || "(no name)" }}
              </button>
            </td>
            <td>{{ patient.gender || "—" }}</td>
            <td>{{ patient.birth_date ?? "—" }}</td>
            <td class="number">{{ patient.observation_count }}</td>
            <td>{{ formatWhen(patient.last_observation_at) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else>No patient matches these filters.</p>
    </template>
  </section>
</template>

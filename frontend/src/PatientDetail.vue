<script setup lang="ts">
import { formatWhen, getPatient, useRequest } from "./api";

const props = defineProps<{ id: number }>();
defineEmits<{ back: [] }>();

const { data: patient, error, loading, retry } = useRequest(() => getPatient(props.id));
</script>

<template>
  <section>
    <button @click="$emit('back')">← All patients</button>
    <p v-if="loading">Loading…</p>
    <p v-else-if="error" role="alert">
      Could not load this patient: {{ error }} <button @click="retry">Retry</button>
    </p>
    <template v-else-if="patient">
      <h1>{{ patient.name || "(no name)" }}</h1>
      <p>
        {{ patient.gender || "Gender unknown" }} · born {{ patient.birth_date ?? "unknown" }} ·
        FHIR id <code>{{ patient.fhir_id }}</code>
      </p>
      <p v-if="!patient.observations.length">No observations for this patient.</p>
      <table v-else>
        <caption>
          {{ patient.observations.length }} observations, most recent first
        </caption>
        <thead>
          <tr>
            <th>Date</th>
            <th>Observation</th>
            <th>Value</th>
            <th>Category</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="observation in patient.observations" :key="observation.id">
            <td>{{ formatWhen(observation.effective_at, observation.effective_date_only) }}</td>
            <td>{{ observation.display || observation.code || "—" }}</td>
            <td>{{ observation.value || "—" }}</td>
            <td>{{ observation.category || "—" }}</td>
            <td>{{ observation.status }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

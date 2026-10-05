<script setup lang="ts">
import { ref } from "vue";
import PatientDetail from "./PatientDetail.vue";
import PatientList from "./PatientList.vue";

// Two screens: local state is enough, no router.
const selectedId = ref<number | null>(null);
</script>

<template>
  <main>
    <PatientDetail
      v-if="selectedId !== null"
      :id="selectedId"
      :key="selectedId"
      @back="selectedId = null"
    />
    <PatientList v-else @select="(id) => (selectedId = id)" />
  </main>
</template>

<style>
/* Readable, not styled: visual polish is out of scope. */
body {
  font-family: system-ui, sans-serif;
  margin: 1.5rem;
}
table {
  border-collapse: collapse;
}
th,
td {
  padding: 0.25rem 0.75rem;
  border-bottom: 1px solid #ddd;
  text-align: left;
}
td.number {
  text-align: right;
}
label {
  margin-right: 1.5rem;
}
caption {
  text-align: left;
  padding: 0.5rem 0;
}
button.link {
  background: none;
  border: none;
  padding: 0;
  color: #0645ad;
  cursor: pointer;
  text-decoration: underline;
  font: inherit;
}
</style>

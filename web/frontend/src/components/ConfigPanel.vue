<script setup lang="ts">
defineProps<{ config: Record<string, unknown> }>();
</script>

<template>
  <section class="rounded-lg border border-slate-700 bg-cardbg p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">运行配置</h3>
    <dl class="grid grid-cols-2 gap-2 text-xs">
      <template v-for="(value, key) in config" :key="String(key)">
        <dt class="text-slate-400">{{ key }}</dt>
        <dd class="truncate text-slate-200">{{ String(value) }}</dd>
      </template>
    </dl>
  </section>
</template>

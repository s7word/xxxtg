<template>
  <div class="ce-toasts" aria-live="polite">
    <div
      v-for="toast in toasts"
      :key="toast.id"
      class="ce-toast"
      :class="'is-' + toast.type"
      @click="dismissToast(toast.id)"
    >
      {{ toast.message }}
    </div>
  </div>
</template>

<script setup>
import { useUi } from '../composables/useUi'

const { toasts, dismissToast } = useUi()
</script>

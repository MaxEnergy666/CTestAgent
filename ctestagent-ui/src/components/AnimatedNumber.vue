<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  value: {
    type: Number,
    default: 0,
  },
  duration: {
    type: Number,
    default: 500,
  },
  decimals: {
    type: Number,
    default: 0,
  },
  prefix: {
    type: String,
    default: '',
  },
  suffix: {
    type: String,
    default: '',
  },
})

const displayValue = ref(Number(props.value || 0))
let rafId = 0

function animateTo(target) {
  const start = displayValue.value
  const delta = target - start
  if (Math.abs(delta) < 1e-6) {
    displayValue.value = target
    return
  }

  const begin = performance.now()
  const duration = Math.max(180, Number(props.duration || 500))

  const step = (now) => {
    const progress = Math.min(1, (now - begin) / duration)
    const eased = 1 - Math.pow(1 - progress, 3)
    displayValue.value = start + delta * eased

    if (progress < 1) {
      rafId = requestAnimationFrame(step)
    }
  }

  cancelAnimationFrame(rafId)
  rafId = requestAnimationFrame(step)
}

watch(
  () => Number(props.value || 0),
  (next) => {
    animateTo(next)
  },
)

const text = computed(() => {
  return `${props.prefix}${displayValue.value.toFixed(props.decimals)}${props.suffix}`
})

onBeforeUnmount(() => {
  cancelAnimationFrame(rafId)
})
</script>

<template>
  <span>{{ text }}</span>
</template>

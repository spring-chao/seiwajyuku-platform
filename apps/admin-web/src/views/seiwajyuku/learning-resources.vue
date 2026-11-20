<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { http } from "@/utils/http";

defineOptions({ name: "LearningResources" });

type Month = { number: number; label: string; qrCount: number };
type Year = { year: number; title: string; months: Month[] };
type Resource = { url: string; qrData: string; qrNo: number; label?: string };
type MonthResources = {
  label: string;
  classTopic: string;
  groupTopic: string;
  extraTopics: string[];
  classQrs: Resource[];
  groupQrs: Resource[];
  extraQrs: Resource[];
  resourceNote?: string;
};

const endpoint = "/api/v1/learning-resources";
const years = ref<Year[]>([]);
const year = ref(1);
const month = ref(1);
const details = ref<MonthResources>();
const loading = ref(false);
const error = ref("");
const currentYear = computed(() => years.value.find(item => item.year === year.value));
const sections = computed(() => {
  const data = details.value;
  if (!data) return [];
  return [
    { title: "班会学习", topics: [data.classTopic], items: data.classQrs },
    { title: "小组学习会", topics: [data.groupTopic], items: data.groupQrs },
    { title: "补充学习内容", topics: data.extraTopics, items: data.extraQrs }
  ].filter(section => section.items.length || section.topics.some(Boolean));
});

function resourceUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}

let selection = 0;
async function loadMonth() {
  const serial = ++selection;
  loading.value = true;
  details.value = undefined;
  error.value = "";
  try {
    const result = await http.request<{ data: MonthResources }>("get", endpoint, {
      params: { year: year.value, month: month.value }
    });
    if (serial === selection) details.value = result.data;
  } catch {
    if (serial === selection) error.value = "本期学习资料加载失败，请重试。";
  } finally {
    if (serial === selection) loading.value = false;
  }
}

async function loadCatalog() {
  loading.value = true;
  error.value = "";
  try {
    const result = await http.request<{ data: { years: Year[] } }>("get", endpoint);
    years.value = result.data.years;
  } catch {
    error.value = "学习资料目录加载失败，请重试。";
    loading.value = false;
    return;
  }
  await loadMonth();
}

function selectYear(value: number) {
  year.value = value;
  month.value = 1;
}

watch([year, month], () => { void loadMonth(); });
onMounted(loadCatalog);
</script>

<template>
  <main class="learning-resources" v-loading="loading">
    <header class="resource-header">
      <h1>盛和塾学习资源中心</h1>
      <p>提高心性、拓展经营</p>
    </header>

    <el-alert v-if="error" :title="error" :closable="false" type="error" show-icon />
    <el-button v-if="error" @click="years.length ? loadMonth() : loadCatalog()">重试</el-button>

    <template v-if="currentYear">
      <nav class="options" aria-label="学习年度">
        <el-button v-for="item in years" :key="item.year" :type="year === item.year ? 'primary' : 'default'"
          :aria-pressed="year === item.year" @click="selectYear(item.year)">{{ item.title }}</el-button>
      </nav>
      <nav class="options months" aria-label="学习期次">
        <el-button v-for="item in currentYear.months" :key="item.number" :type="month === item.number ? 'primary' : 'default'"
          :aria-pressed="month === item.number" @click="month = item.number">{{ item.label }}</el-button>
      </nav>
    </template>

    <template v-if="details">
      <h2>{{ currentYear?.title }} · {{ details.label }}</h2>
      <el-alert v-if="details.resourceNote" :title="details.resourceNote" type="info" :closable="false" />
      <el-empty v-if="!sections.length" description="本期暂无学习资料" />
      <el-card v-for="section in sections" :key="section.title" shadow="never" class="resource-section">
        <template #header><strong>{{ section.title }}</strong></template>
        <p v-for="(topic, index) in section.topics.filter(Boolean)" :key="index" class="topic">{{ topic }}</p>
        <div v-if="section.items.length" class="resource-grid">
          <div v-for="(item, index) in section.items" :key="index" class="qr-card">
            <el-image v-if="item.qrData" :src="item.qrData" :preview-src-list="[item.qrData]"
              :alt="item.label || '学习资料二维码'" fit="contain" preview-teleported class="qr-image" />
            <span>{{ item.label || ('学习资料 ' + (index + 1)) }}</span>
            <a v-if="resourceUrl(item.url)" :href="resourceUrl(item.url)" target="_blank" rel="noopener noreferrer">打开资料链接 ↗</a>
          </div>
        </div>
        <p v-else class="muted">本期此项没有配置二维码。</p>
      </el-card>
    </template>
  </main>
</template>

<style scoped>
.learning-resources { display: grid; gap: 16px; }
.resource-header { padding: 24px; border-radius: 12px; background: var(--el-color-primary-light-9); }
.resource-header h1 { margin: 0 0 8px; font-size: 26px; }
.resource-header p, .topic { margin: 0; }
.options { display: flex; gap: 8px; flex-wrap: wrap; }
.options :deep(.el-button) { margin-left: 0; }
.resource-section { border-radius: 10px; }
.topic { white-space: pre-wrap; line-height: 1.7; }
.muted { color: var(--el-text-color-secondary); margin-top: 8px; }
.resource-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(152px, 1fr)); gap: 12px; margin-top: 12px; }
.qr-card { display: grid; justify-items: center; gap: 8px; padding: 12px; border: 1px solid var(--el-border-color-light); border-radius: 8px; }
.qr-image { width: 116px; height: 116px; background: #fff; cursor: zoom-in; }
.qr-card a { color: var(--el-color-primary); }
</style>


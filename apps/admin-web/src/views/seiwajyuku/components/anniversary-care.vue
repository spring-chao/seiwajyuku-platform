<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { http } from "@/utils/http";

type Anniversary = {
  member_id: number;
  member_name: string;
  org_name: string;
  class_name: string | null;
  years: number;
};

type Draft = {
  draft: string;
  memories: { id: string; title: string; occurred_on: string }[];
  generation_status: string;
};

const items = ref<Anniversary[]>([]);
const loading = ref(false);
const listError = ref(false);
const selected = ref<Anniversary>();
const visible = ref(false);
const generating = ref(false);
const result = ref<Draft>();
const draft = ref("");
const draftError = ref("");

async function load() {
  loading.value = true;
  listError.value = false;
  try {
    const response = await http.request<{ data: { items: Anniversary[] } }>(
      "get",
      "/api/v1/members/anniversary-care/today"
    );
    items.value = response.data.items;
  } catch {
    listError.value = true;
  } finally {
    loading.value = false;
  }
}

async function generate() {
  const member = selected.value;
  if (!member || generating.value) return;
  generating.value = true;
  draftError.value = "";
  try {
    const response = await http.request<{ data: Draft }>(
      "post",
      `/api/v1/members/${member.member_id}/anniversary-greeting-draft`,
      { timeout: 20000 }
    );
    result.value = response.data;
    if (response.data.generation_status === "GENERATED") {
      draft.value = response.data.draft;
    } else {
      draftError.value = "暂未生成符合要求的草稿，请重试。";
    }
  } catch {
    draftError.value = "生成失败，请刷新周年名单后重试。";
  } finally {
    generating.value = false;
  }
}

function open(memberId: number) {
  const member = items.value.find(item => item.member_id === memberId);
  if (!member) return;
  selected.value = member;
  draft.value = "";
  draftError.value = "";
  result.value = undefined;
  visible.value = true;
}

async function copy() {
  if (/[我你]/u.test(draft.value)) {
    ElMessage.warning("请使用「您」或「您和学长们」，并检查是否含有「我」或「你」。");
    return;
  }
  try {
    await navigator.clipboard.writeText(draft.value);
    ElMessage.success("已复制周年关怀文案");
  } catch {
    ElMessage.warning("复制失败，可选中文案手动复制。");
  }
}

onMounted(load);
</script>

<template>
  <section v-loading="loading" aria-label="入塾周年关怀">
    <div class="anniversary-heading">
      <h2>入塾周年关怀</h2>
      <el-button link type="primary" :disabled="loading" @click="load">刷新</el-button>
    </div>
    <el-alert v-if="listError" title="暂时无法读取周年名单" type="warning" :closable="false" />
    <el-table v-else :data="items" row-key="member_id" empty-text="今日暂无入塾周年关怀">
      <el-table-column prop="member_name" label="学长" min-width="115" />
      <el-table-column prop="org_name" label="所属组织" min-width="130" />
      <el-table-column prop="class_name" label="班级" min-width="110" />
      <el-table-column label="入塾周年" width="105">
        <template #default="{ row }">{{ row.years }} 周年</template>
      </el-table-column>
      <el-table-column label="关怀" width="120">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="generating" @click="open(row.member_id)">准备文案</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-drawer
      v-model="visible"
      :title="`${selected?.member_name || ''}学长 · 入塾${selected?.years || ''}周年`"
      size="min(94vw, 560px)"
    >
      <div class="anniversary-draft">
        <template v-if="result">
          <h3>过往点滴</h3>
          <ul v-if="result.memories.length">
            <li v-for="memory in result.memories" :key="memory.id">
              {{ memory.occurred_on }} · {{ memory.title }}
            </li>
          </ul>
          <p v-else>暂无可用于回顾的已核实参与记录。</p>
        </template>
        <el-alert v-if="draftError" :title="draftError" type="warning" :closable="false" />
        <el-input v-if="draft" v-model="draft" aria-label="周年关怀文案" type="textarea" :rows="10" />
        <div>
          <el-button type="primary" :loading="generating" @click="generate">
            {{ draft ? "重新生成" : "用大模型生成文案" }}
          </el-button>
          <el-button :disabled="!draft || generating" @click="copy">复制文案</el-button>
        </div>
      </div>
    </el-drawer>
  </section>
</template>

<style scoped>
.anniversary-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.anniversary-draft {
  display: grid;
  gap: 16px;
}

.anniversary-draft ul {
  padding-left: 22px;
  list-style: disc;
}
</style>

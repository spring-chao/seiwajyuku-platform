<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  applyDuplicateClassCleanup,
  createLearningOrgUnit,
  deactivateLearningOrgUnit,
  getLearningGroupMemberTransferOptions,
  getLearningOrgManagement,
  getSystemEnvironment,
  moveLearningOrgUnit,
  previewLearningOrgMove,
  transferLearningGroupMember,
  type LearningGroupMemberTransferOptions,
  type LearningOrgManagement,
  type ManagedLearningOrgUnit
} from "@/api/seiwajyuku";

defineOptions({ name: "OrganizationManagement" });

const loading = ref(false);
const saving = ref(false);
const data = ref<LearningOrgManagement>({ units: [], centers: [], classes: [] });
const writeEnabled = ref(false);
const centerFilter = ref("");
const classFilter = ref("");
const statusFilter = ref("ACTIVE");
const createVisible = ref(false);
const moveVisible = ref(false);
const memberTransferVisible = ref(false);
const movingUnit = ref<ManagedLearningOrgUnit>();
const memberTransferSource = ref<ManagedLearningOrgUnit>();
const memberTransferData = ref<LearningGroupMemberTransferOptions>();
const createForm = reactive({
  unit_type: "CLASS" as "CLASS" | "GROUP",
  name: "",
  parent_id: ""
});
const moveForm = reactive({ target_parent_id: "", reason: "" });
const memberTransferForm = reactive({
  targetByMember: {} as Record<number, string>,
  reason: "清理重复小组关联"
});
const kunshanYanwuClasses = ["炎武一班", "炎武二班", "炎武三班", "炎武四班"];

function classReferenceScore(item: ManagedLearningOrgUnit) {
  const counts = item.reference_counts;
  return (
    counts.active_member_relations * 1_000_000 +
    counts.active_children * 1_000 +
    counts.active_events
  );
}

function preferClass(
  candidate: ManagedLearningOrgUnit,
  current: ManagedLearningOrgUnit
) {
  const scoreDifference =
    classReferenceScore(candidate) - classReferenceScore(current);
  if (scoreDifference !== 0) return scoreDifference > 0;
  const candidateCreatedAt = candidate.created_at || "9999";
  const currentCreatedAt = current.created_at || "9999";
  if (candidateCreatedAt !== currentCreatedAt) {
    return candidateCreatedAt < currentCreatedAt;
  }
  return candidate.id < current.id;
}

const deduplicatedCenters = computed(() => {
  const centerScores = new Map<string, number>();
  data.value.units
    .filter(item => item.unit_type === "CLASS")
    .forEach(item => {
      const centerId = item.parent_id || "";
      centerScores.set(
        centerId,
        (centerScores.get(centerId) || 0) + classReferenceScore(item)
      );
    });
  const winners = new Map<string, { id: string; name: string }>();
  data.value.centers.forEach(item => {
    const current = winners.get(item.name);
    if (
      !current ||
      (centerScores.get(item.id) || 0) > (centerScores.get(current.id) || 0) ||
      ((centerScores.get(item.id) || 0) ===
        (centerScores.get(current.id) || 0) &&
        item.id < current.id)
    ) {
      winners.set(item.name, item);
    }
  });
  return Array.from(winners.values()).sort((left, right) =>
    left.name.localeCompare(right.name, "zh-CN")
  );
});

const selectedCenterIds = computed(() => {
  if (!centerFilter.value) return undefined;
  const selectedCenter = data.value.centers.find(
    item => item.id === centerFilter.value
  );
  const ids = new Set(
    data.value.centers
      .filter(item => !selectedCenter || item.name === selectedCenter.name)
      .map(item => item.id)
  );
  ids.add(centerFilter.value);
  return ids;
});

const deduplicatedClasses = computed(() => {
  const centerNames = new Map(
    data.value.centers.map(item => [item.id, item.name])
  );
  const winners = new Map<string, ManagedLearningOrgUnit>();
  data.value.classes.forEach(item => {
    const centerScope =
      centerNames.get(item.parent_id || "") || item.parent_id || "";
    const key = `${centerScope}\u0000${item.name.trim()}`;
    const current = winners.get(key);
    if (!current || preferClass(item, current)) {
      winners.set(key, item);
    }
  });
  return Array.from(winners.values()).sort((left, right) => {
    const leftCenter = centerNames.get(left.parent_id || "") || "";
    const rightCenter = centerNames.get(right.parent_id || "") || "";
    return (
      leftCenter.localeCompare(rightCenter, "zh-CN") ||
      left.name.localeCompare(right.name, "zh-CN") ||
      left.id.localeCompare(right.id)
    );
  });
});

const classFilterOptions = computed(() =>
  deduplicatedClasses.value.filter(item => {
    if (statusFilter.value === "ACTIVE" && !item.is_active) return false;
    if (statusFilter.value === "INACTIVE" && item.is_active) return false;
    return (
      !selectedCenterIds.value ||
      selectedCenterIds.value.has(item.parent_id || "")
    );
  })
);

const filteredUnits = computed(() => {
  const classesById = new Map(
    data.value.units
      .filter(item => item.unit_type === "CLASS")
      .map(item => [item.id, item])
  );
  const selectedClass = data.value.classes.find(
    item => item.id === classFilter.value
  );
  return data.value.units.filter(item => {
    if (statusFilter.value === "ACTIVE" && !item.is_active) return false;
    if (statusFilter.value === "INACTIVE" && item.is_active) return false;
    const parentClass =
      item.unit_type === "GROUP"
        ? classesById.get(item.parent_id || "")
        : undefined;
    if (centerFilter.value) {
      const belongsToCenter =
        item.unit_type === "CLASS"
          ? selectedCenterIds.value?.has(item.parent_id || "")
          : selectedCenterIds.value?.has(parentClass?.parent_id || "");
      if (!belongsToCenter) return false;
    }
    if (classFilter.value) {
      if (item.id === classFilter.value || item.parent_id === classFilter.value) {
        return true;
      }
      // 历史重复班级节点可能挂有真实小组。选择规范班级时，按同一
      // 分中心范围内的班级名称匹配，避免这些小组被筛选器误隐藏。
      return Boolean(
        selectedClass &&
          parentClass &&
          parentClass.name === selectedClass.name &&
          parentClass.parent_id === selectedClass.parent_id
      );
    }
    return true;
  });
});

watch([centerFilter, statusFilter], () => {
  if (
    classFilter.value &&
    !classFilterOptions.value.some(item => item.id === classFilter.value)
  ) {
    classFilter.value = "";
  }
});

const availableParents = computed(() =>
  createForm.unit_type === "CLASS"
    ? deduplicatedCenters.value
    : deduplicatedClasses.value.filter(
        item =>
          !selectedCenterIds.value ||
          selectedCenterIds.value.has(item.parent_id || "")
      )
);

function errorText(error: unknown) {
  const candidate = error as any;
  return candidate?.response?.data?.detail || candidate?.message || "操作失败";
}

function typeLabel(type: string) {
  return type === "CLASS" ? "班级" : "小组";
}

function referenceText(item: ManagedLearningOrgUnit) {
  const counts = item.reference_counts;
  return `在册关系 ${counts.active_member_relations}；子组织 ${counts.active_children}；活动 ${counts.active_events}`;
}

function asManagedUnit(item: unknown) {
  return item as ManagedLearningOrgUnit;
}

async function load() {
  loading.value = true;
  try {
    const [management, environment] = await Promise.all([
      getLearningOrgManagement(),
      getSystemEnvironment()
    ]);
    data.value = management.data;
    writeEnabled.value =
      !environment.deployment_read_only &&
      (!environment.production || environment.production_mutations_allowed);
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    loading.value = false;
  }
}

function openCreate(unitType: "CLASS" | "GROUP") {
  const selectedParentId =
    unitType === "GROUP" &&
    classFilterOptions.value.some(item => item.id === classFilter.value)
      ? classFilter.value
      : "";
  Object.assign(createForm, {
    unit_type: unitType,
    name: "",
    parent_id: selectedParentId
  });
  createVisible.value = true;
}

async function submitCreate() {
  const name = createForm.name.trim();
  if (!name || !createForm.parent_id) {
    ElMessage.warning("请填写名称并选择父级组织");
    return;
  }
  const label = typeLabel(createForm.unit_type);
  await ElMessageBox.confirm(
    `确认新增${label}“${name}”？新增后可在学员编辑页面选择。`,
    `新增${label}`,
    { type: "warning", confirmButtonText: "确认新增" }
  );
  saving.value = true;
  try {
    await createLearningOrgUnit({
      ...createForm,
      name,
      confirmation: `确认新增${label}：${name}`
    });
    const parent = data.value.units.find(item => item.id === createForm.parent_id);
    if (createForm.unit_type === "GROUP" && parent) {
      centerFilter.value = parent.parent_id || "";
      classFilter.value = parent.id;
      statusFilter.value = "ACTIVE";
    }
    ElMessage.success(
      `${label}“${name}”已新增并记录审计，已定位到所属${createForm.unit_type === "GROUP" ? "班级" : "分中心"}`
    );
    createVisible.value = false;
    await load();
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    saving.value = false;
  }
}

function openMove(item: ManagedLearningOrgUnit) {
  movingUnit.value = item;
  Object.assign(moveForm, { target_parent_id: item.parent_id || "", reason: "" });
  moveVisible.value = true;
}

async function openMemberTransfer(item: ManagedLearningOrgUnit) {
  memberTransferSource.value = item;
  memberTransferData.value = undefined;
  memberTransferForm.targetByMember = {};
  memberTransferForm.reason = "清理重复小组关联";
  try {
    const response = await getLearningGroupMemberTransferOptions(item.id);
    memberTransferData.value = response.data;
    memberTransferVisible.value = true;
  } catch (error) {
    ElMessage.error(errorText(error));
  }
}

async function transferMember(member: any) {
  const source = memberTransferSource.value;
  const transfer = memberTransferData.value;
  const targetId = memberTransferForm.targetByMember[member.member_id];
  const target = transfer?.target_groups.find(item => item.id === targetId);
  if (!source || !target || memberTransferForm.reason.trim().length < 6) {
    ElMessage.warning("请选择目标小组并填写至少6个字符的迁移依据");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确认将“${member.name}”从“${source.name}”迁移到“${target.name}”？原关系会保留为历史记录。`,
      "确认迁移小组关系",
      { type: "warning", confirmButtonText: "确认迁移" }
    );
    saving.value = true;
    await transferLearningGroupMember(source.id, {
      member_id: member.member_id,
      target_group_org_unit_id: target.id,
      reason: memberTransferForm.reason.trim(),
      confirmation: `确认将${member.name}从${source.name}转至${target.name}`
    });
    ElMessage.success(`${member.name}已迁移至${target.name}`);
    await load();
    const response = await getLearningGroupMemberTransferOptions(source.id);
    memberTransferData.value = response.data;
    memberTransferForm.targetByMember = {};
    if (!response.data.members.length) memberTransferVisible.value = false;
  } catch (error) {
    if (error !== "cancel") ElMessage.error(errorText(error));
  } finally {
    saving.value = false;
  }
}

async function cleanKunshanYanwuDuplicates() {
  try {
    await ElMessageBox.confirm(
      "将保留每个炎武班的正式主班级，迁移无冲突小组及班级关系，并停用重复班级。若发现同名小组或其他业务引用，系统会整体取消，不会部分迁移。",
      "归并昆山炎武重复班级",
      { type: "warning", confirmButtonText: "确认归并" }
    );
    saving.value = true;
    const response = await applyDuplicateClassCleanup({
      class_names: kunshanYanwuClasses,
      confirmation: "确认合并昆山炎武一至四班重复组织"
    });
    ElMessage.success(`已归并 ${response.data.deactivated_duplicate_classes} 个重复班级`);
    await load();
  } catch (error) {
    if (error !== "cancel") ElMessage.error(errorText(error));
  } finally {
    saving.value = false;
  }
}

async function submitMove() {
  if (!movingUnit.value || !moveForm.target_parent_id || moveForm.reason.trim().length < 6) {
    ElMessage.warning("请选择目标分中心并填写至少6个字符的确认依据");
    return;
  }
  saving.value = true;
  try {
    const preview = await previewLearningOrgMove(
      movingUnit.value.id,
      moveForm.target_parent_id
    );
    const counts = preview.data.reference_counts;
    await ElMessageBox.confirm(
      `调整后班级及其 ${counts.active_children} 个子组织将纳入“${preview.data.target_parent_name}”范围；当前关联在册学员 ${counts.active_member_relations} 人、活动 ${counts.active_events} 个。是否继续？`,
      "确认调整班级归属",
      { type: "warning", confirmButtonText: "确认调整" }
    );
    await moveLearningOrgUnit(movingUnit.value.id, {
      target_parent_id: moveForm.target_parent_id,
      reason: moveForm.reason.trim(),
      confirmation: preview.data.confirmation
    });
    ElMessage.success(
      `班级归属已调整，已自动同步 ${preview.data.reference_counts.active_member_relations} 条在册关系`
    );
    moveVisible.value = false;
    await load();
  } catch (error) {
    if (error !== "cancel") ElMessage.error(errorText(error));
  } finally {
    saving.value = false;
  }
}

async function deactivate(item: ManagedLearningOrgUnit) {
  const label = typeLabel(item.unit_type);
  try {
    const prompt = await ElMessageBox.prompt(
      `仅当没有在册学员、子组织和活动关联时才能停用“${item.name}”。请输入停用依据：`,
      `停用${label}`,
      {
        inputValidator: value => value.trim().length >= 6 || "至少输入6个字符",
        confirmButtonText: "下一步"
      }
    );
    await ElMessageBox.confirm(
      `确认停用${label}“${item.name}”？历史记录会保留。`,
      "最终确认",
      { type: "warning", confirmButtonText: "确认停用" }
    );
    saving.value = true;
    await deactivateLearningOrgUnit(item.id, {
      reason: prompt.value.trim(),
      confirmation: `确认停用${label}：${item.name}`
    });
    ElMessage.success(`${label}已停用并保留历史`);
    await load();
  } catch (error) {
    if (error !== "cancel") ElMessage.error(errorText(error));
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="organization-page">
    <section class="hero">
      <div>
        <p>系统设置 · 组织主数据</p>
        <h1>班级与小组管理</h1>
        <span>这里是分中心、班级和小组关系的唯一维护入口；调整班级归属后，关联学员及全系统当前视图会自动同步。</span>
      </div>
      <div class="hero-actions">
        <el-button type="warning" :disabled="!writeEnabled || saving" @click="cleanKunshanYanwuDuplicates">归并炎武重复班级</el-button>
        <el-button :disabled="!writeEnabled" @click="openCreate('GROUP')">新增小组</el-button>
        <el-button type="primary" :disabled="!writeEnabled" @click="openCreate('CLASS')">新增班级</el-button>
      </div>
    </section>

    <el-alert
      v-if="!writeEnabled"
      title="当前生产写入门禁未开启，组织数据仅可查看"
      type="warning"
      :closable="false"
      show-icon
    />

    <section class="content-card">
      <div class="filters">
        <el-select v-model="centerFilter" clearable placeholder="全部分中心">
          <el-option v-for="item in deduplicatedCenters" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-select v-model="classFilter" clearable filterable placeholder="全部班级">
          <el-option v-for="item in classFilterOptions" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-select v-model="statusFilter">
          <el-option label="启用中" value="ACTIVE" />
          <el-option label="已停用" value="INACTIVE" />
          <el-option label="全部状态" value="ALL" />
        </el-select>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>

      <el-table v-loading="loading" :data="filteredUnits" stripe>
        <el-table-column prop="name" label="名称" min-width="180" />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">{{ typeLabel(row.unit_type) }}</template>
        </el-table-column>
        <el-table-column prop="parent_name" label="所属分中心 / 班级" min-width="200" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? "启用" : "停用" }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="当前关联" min-width="260">
          <template #default="{ row }">{{ referenceText(asManagedUnit(row)) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.unit_type === 'CLASS' && row.is_active" link type="primary" :disabled="!writeEnabled" @click="openMove(asManagedUnit(row))">调整归属</el-button>
            <el-button v-if="row.unit_type === 'GROUP' && row.is_active && row.reference_counts.active_member_relations" link type="primary" :disabled="!writeEnabled || saving" @click="openMemberTransfer(asManagedUnit(row))">查看并迁移（{{ row.reference_counts.active_member_relations }}）</el-button>
            <el-button v-if="row.is_active" link type="danger" :disabled="!writeEnabled || saving" @click="deactivate(asManagedUnit(row))">停用</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="createVisible" :title="`新增${typeLabel(createForm.unit_type)}`" width="560px">
      <el-form label-position="top">
        <el-form-item :label="`${typeLabel(createForm.unit_type)}名称`">
          <el-input v-model="createForm.name" maxlength="255" />
        </el-form-item>
        <el-form-item :label="createForm.unit_type === 'CLASS' ? '所属分中心' : '所属班级'">
          <el-select v-model="createForm.parent_id" filterable style="width: 100%">
            <el-option v-for="item in availableParents" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">新增并记录审计</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="moveVisible" :title="`调整${movingUnit?.name || '班级'}归属`" width="620px">
      <el-form label-position="top">
        <el-form-item label="目标分中心">
          <el-select v-model="moveForm.target_parent_id" style="width: 100%">
            <el-option v-for="item in data.centers" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="确认依据">
          <el-input v-model="moveForm.reason" type="textarea" :rows="3" placeholder="例如：业务负责人确认该班级属于吴江分中心" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="moveVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitMove">预检并调整</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="memberTransferVisible" :title="`迁移${memberTransferSource?.name || '小组'}关联学员`" width="760px">
      <p class="transfer-hint">请选择同班目标小组后逐人迁移；迁移完成即可停用来源小组，原关系会保留在历史中。</p>
      <el-table :data="memberTransferData?.members || []" size="small">
        <el-table-column prop="name" label="学员" min-width="120" />
        <el-table-column prop="member_code" label="编号" min-width="140" />
        <el-table-column prop="phone_masked" label="手机号（脱敏）" min-width="130">
          <template #default="{ row }">{{ row.phone_masked || "—" }}</template>
        </el-table-column>
        <el-table-column label="目标小组" min-width="180">
          <template #default="{ row }">
            <el-select v-model="memberTransferForm.targetByMember[row.member_id]" filterable placeholder="请选择目标小组" style="width: 100%">
              <el-option v-for="target in memberTransferData?.target_groups || []" :key="target.id" :label="target.name" :value="target.id" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="95">
          <template #default="{ row }">
            <el-button link type="primary" :loading="saving" @click="transferMember(row)">迁移</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="memberTransferData && !memberTransferData.members.length" description="该小组已无在册关联学员，可以关闭后执行停用" />
      <el-form label-position="top" class="transfer-reason">
        <el-form-item label="迁移依据">
          <el-input v-model="memberTransferForm.reason" type="textarea" :rows="2" maxlength="1000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="memberTransferVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.organization-page { padding: 24px; background: #f3f6f8; min-height: 100%; }
.hero { display: flex; justify-content: space-between; gap: 24px; align-items: flex-end; padding: 28px 32px; color: #fff; border-radius: 22px; background: linear-gradient(120deg, #123f36, #267356); }
.hero p { margin: 0 0 8px; color: #87e4c2; }
.hero h1 { margin: 0 0 12px; font-size: 34px; }
.hero span { color: #d8f4ea; }
.hero-actions { display: flex; gap: 12px; flex-shrink: 0; }
.content-card { margin-top: 20px; padding: 22px; border-radius: 16px; background: #fff; }
.filters { display: flex; gap: 12px; margin-bottom: 18px; }
.filters .el-select { width: 220px; }
.el-alert { margin-top: 18px; }
.transfer-hint { margin: 0 0 14px; color: #677a73; }
.transfer-reason { margin-top: 18px; }
@media (max-width: 860px) { .hero { align-items: flex-start; flex-direction: column; } .filters { flex-wrap: wrap; } }
</style>

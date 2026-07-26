<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, type FormInstance, type FormRules } from "element-plus";
import { useUserStoreHook } from "@/store/modules/user";
import {
  createMember,
  getMembers,
  getOrgUnits,
  type Member,
  type OrgUnit
} from "@/api/seiwajyuku";

defineOptions({ name: "MemberManagement" });

const loading = ref(false);
const saving = ref(false);
const dialogVisible = ref(false);
const selectedOrg = ref("");
const keyword = ref("");
const rows = ref<Member[]>([]);
const orgs = ref<OrgUnit[]>([]);
const formRef = ref<FormInstance>();
const canManage = computed(() =>
  useUserStoreHook().permissions.includes("members:manage")
);
const centerOrgs = computed(() =>
  orgs.value.filter(item => item.unit_type === "REGIONAL_CENTER")
);
const filteredRows = computed(() => {
  const term = keyword.value.trim().toLowerCase();
  if (!term) return rows.value;
  return rows.value.filter(item =>
    [item.name, item.member_code, item.company_name, item.phone_last4]
      .filter(Boolean)
      .some(value => String(value).toLowerCase().includes(term))
  );
});

const form = reactive({
  member_code: "",
  name: "",
  org_unit_id: "",
  phone: "",
  company_name: ""
});
const rules: FormRules = {
  member_code: [{ required: true, message: "请输入学员编号", trigger: "blur" }],
  name: [{ required: true, message: "请输入姓名", trigger: "blur" }],
  org_unit_id: [{ required: true, message: "请选择分中心", trigger: "change" }],
  phone: [
    { required: true, message: "请输入手机号", trigger: "blur" },
    {
      pattern: /^1\d{10}$/,
      message: "请输入 11 位手机号",
      trigger: "blur"
    }
  ]
};

function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || "操作失败";
}

async function load() {
  loading.value = true;
  try {
    const [members, organizations] = await Promise.all([
      getMembers(selectedOrg.value || undefined),
      getOrgUnits()
    ]);
    rows.value = members.data;
    orgs.value = organizations.data;
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  Object.assign(form, {
    member_code: "",
    name: "",
    org_unit_id: selectedOrg.value,
    phone: "",
    company_name: ""
  });
  dialogVisible.value = true;
}

async function submit() {
  if (!(await formRef.value?.validate())) return;
  saving.value = true;
  try {
    await createMember({
      member_code: form.member_code.trim(),
      name: form.name.trim(),
      org_unit_id: form.org_unit_id,
      phone: form.phone.trim(),
      company_name: form.company_name.trim() || undefined
    });
    ElMessage.success("学员已创建，手机号已加密保存");
    dialogVisible.value = false;
    await load();
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="member-page" v-loading="loading">
    <section class="page-head">
      <div>
        <p>关怀试点 · 主数据</p>
        <h1>学员管理</h1>
        <span>手机号加密保存；列表、普通查询和后续任务默认只显示脱敏号码。</span>
      </div>
      <el-button v-if="canManage" type="primary" size="large" @click="openCreate">
        新增学员
      </el-button>
    </section>

    <el-alert
      title="先选择一个分中心开展小范围试点；正式批量导入前需提供已填写的学员基本信息表。"
      type="warning"
      :closable="false"
      show-icon
    />

    <el-card shadow="never">
      <div class="toolbar">
        <el-select
          v-model="selectedOrg"
          clearable
          placeholder="全部分中心"
          @change="load"
        >
          <el-option
            v-for="org in centerOrgs"
            :key="org.id"
            :label="org.name"
            :value="org.id"
          />
        </el-select>
        <el-input
          v-model="keyword"
          clearable
          placeholder="搜索姓名、编号、企业或手机后四位"
        />
        <span class="result-count">共 {{ filteredRows.length }} 人</span>
      </div>

      <el-table :data="filteredRows" stripe empty-text="暂无学员数据">
        <el-table-column prop="member_code" label="学员编号" min-width="130" />
        <el-table-column prop="name" label="姓名" min-width="110" />
        <el-table-column prop="org_name" label="所属分中心" min-width="140" />
        <el-table-column prop="company_name" label="企业" min-width="180">
          <template #default="{ row }">{{ row.company_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="phone_masked" label="手机号（脱敏）" min-width="150" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'">
              {{ row.status === "ACTIVE" ? "在册" : row.status }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" title="新增试点学员" width="560px">
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
      >
        <div class="form-grid">
          <el-form-item label="学员编号" prop="member_code">
            <el-input v-model="form.member_code" placeholder="如 SZ-WJ-0001" />
          </el-form-item>
          <el-form-item label="姓名" prop="name">
            <el-input v-model="form.name" />
          </el-form-item>
          <el-form-item label="所属分中心" prop="org_unit_id">
            <el-select v-model="form.org_unit_id" placeholder="请选择">
              <el-option
                v-for="org in centerOrgs"
                :key="org.id"
                :label="org.name"
                :value="org.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="手机号" prop="phone">
            <el-input v-model="form.phone" maxlength="11" />
          </el-form-item>
          <el-form-item class="full" label="企业名称" prop="company_name">
            <el-input v-model="form.company_name" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">
          加密保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.member-page {
  display: grid;
  gap: 18px;
  padding: 20px;
}
.page-head {
  display: flex;
  align-items: end;
  justify-content: space-between;
  padding: 28px;
  color: #f6fff9;
  background: linear-gradient(125deg, #123c2e, #25704e);
  border-radius: 18px;
}
.page-head p {
  margin: 0 0 8px;
  color: #9fe0bd;
  letter-spacing: 0.14em;
}
.page-head h1 {
  margin: 0 0 10px;
  font-size: 30px;
}
.page-head span {
  color: #cbe9d8;
}
.toolbar {
  display: grid;
  grid-template-columns: 220px minmax(260px, 1fr) auto;
  gap: 14px;
  align-items: center;
  margin-bottom: 18px;
}
.result-count {
  color: var(--el-text-color-secondary);
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 18px;
}
.form-grid .full {
  grid-column: 1 / -1;
}
.form-grid :deep(.el-select) {
  width: 100%;
}
@media (max-width: 760px) {
  .page-head {
    align-items: flex-start;
    gap: 20px;
  }
  .toolbar,
  .form-grid {
    grid-template-columns: 1fr;
  }
  .form-grid .full {
    grid-column: auto;
  }
}
</style>

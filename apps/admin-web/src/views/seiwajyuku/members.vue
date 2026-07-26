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
  name: "",
  org_unit_id: "",
  phone: "",
  company_name: "",
  gender: "",
  district: "",
  company_address: "",
  class_name: "",
  group_name: "",
  birthday: "",
  join_date: "",
  study_start_date: "",
  membership_years: undefined as number | undefined,
  renewal_month: "",
  status: "ACTIVE",
  position: "",
  referrer: "",
  referrer_center: "",
  industry_category: "",
  industry: "",
  company_products: "",
  annual_sales: "",
  company_size: "",
  profit_margin: "",
  notes: ""
});
const rules: FormRules = {
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
    name: "",
    org_unit_id: selectedOrg.value,
    phone: "",
    company_name: "",
    gender: "",
    district: "",
    company_address: "",
    class_name: "",
    group_name: "",
    birthday: "",
    join_date: "",
    study_start_date: "",
    membership_years: undefined,
    renewal_month: "",
    status: "ACTIVE",
    position: "",
    referrer: "",
    referrer_center: "",
    industry_category: "",
    industry: "",
    company_products: "",
    annual_sales: "",
    company_size: "",
    profit_margin: "",
    notes: ""
  });
  dialogVisible.value = true;
}

async function submit() {
  if (!(await formRef.value?.validate())) return;
  saving.value = true;
  try {
    await createMember({
      name: form.name.trim(),
      org_unit_id: form.org_unit_id,
      phone: form.phone.trim(),
      company_name: form.company_name.trim() || undefined,
      gender: form.gender || undefined,
      district: form.district.trim() || undefined,
      company_address: form.company_address.trim() || undefined,
      class_name: form.class_name.trim() || undefined,
      group_name: form.group_name.trim() || undefined,
      birthday: form.birthday || undefined,
      join_date: form.join_date || undefined,
      study_start_date: form.study_start_date || undefined,
      membership_years: form.membership_years,
      renewal_month: form.renewal_month || undefined,
      status: form.status,
      position: form.position.trim() || undefined,
      referrer: form.referrer.trim() || undefined,
      referrer_center: form.referrer_center.trim() || undefined,
      industry_category: form.industry_category.trim() || undefined,
      industry: form.industry.trim() || undefined,
      company_products: form.company_products.trim() || undefined,
      annual_sales: form.annual_sales.trim() || undefined,
      company_size: form.company_size.trim() || undefined,
      profit_margin: form.profit_margin.trim() || undefined,
      notes: form.notes.trim() || undefined
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
        <el-table-column prop="name" label="姓名" min-width="110" />
        <el-table-column prop="org_name" label="所属分中心" min-width="140" />
        <el-table-column prop="class_name" label="班级" min-width="120">
          <template #default="{ row }">{{ row.class_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="group_name" label="组名" min-width="110">
          <template #default="{ row }">{{ row.group_name || "—" }}</template>
        </el-table-column>
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

    <el-dialog
      v-model="dialogVisible"
      title="新增学员"
      width="1180px"
      class="member-dialog"
    >
      <p class="form-hint">
        姓名、分中心和手机号为必填项；年销售额与利润率按敏感信息加密保存。
      </p>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
      >
        <div class="form-grid">
          <el-form-item label="姓名" prop="name">
            <el-input v-model="form.name" />
          </el-form-item>
          <el-form-item label="分中心" prop="org_unit_id">
            <el-select v-model="form.org_unit_id" placeholder="请选择">
              <el-option
                v-for="org in centerOrgs"
                :key="org.id"
                :label="org.name"
                :value="org.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="公司名称" prop="company_name">
            <el-input v-model="form.company_name" />
          </el-form-item>
          <el-form-item label="手机号" prop="phone">
            <el-input v-model="form.phone" maxlength="11" />
          </el-form-item>
          <el-form-item label="隶属区">
            <el-input v-model="form.district" />
          </el-form-item>
          <el-form-item label="公司地址">
            <el-input v-model="form.company_address" />
          </el-form-item>
          <el-form-item label="性别">
            <el-select v-model="form.gender" clearable placeholder="请选择">
              <el-option label="男" value="MALE" />
              <el-option label="女" value="FEMALE" />
              <el-option label="其他/未说明" value="UNSPECIFIED" />
            </el-select>
          </el-form-item>
          <el-form-item label="班级">
            <el-input v-model="form.class_name" />
          </el-form-item>
          <el-form-item label="行业分类">
            <el-input v-model="form.industry_category" />
          </el-form-item>
          <el-form-item label="生日">
            <el-date-picker
              v-model="form.birthday"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="YYYY-MM-DD"
            />
          </el-form-item>
          <el-form-item label="组名">
            <el-input v-model="form.group_name" />
          </el-form-item>
          <el-form-item label="行业">
            <el-input v-model="form.industry" />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="form.status">
              <el-option label="在册" value="ACTIVE" />
              <el-option label="停用" value="INACTIVE" />
              <el-option label="暂停" value="SUSPENDED" />
            </el-select>
          </el-form-item>
          <el-form-item label="职务">
            <el-input v-model="form.position" />
          </el-form-item>
          <el-form-item label="公司产品">
            <el-input v-model="form.company_products" />
          </el-form-item>
          <el-form-item label="入塾日期">
            <el-date-picker
              v-model="form.join_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="YYYY-MM-DD"
            />
          </el-form-item>
          <el-form-item label="续费月份">
            <el-date-picker
              v-model="form.renewal_month"
              type="month"
              value-format="YYYY-MM"
              placeholder="YYYY-MM"
            />
          </el-form-item>
          <el-form-item label="年销售额">
            <el-input v-model="form.annual_sales" placeholder="按原系统口径填写" />
          </el-form-item>
          <el-form-item label="开始学习时间">
            <el-date-picker
              v-model="form.study_start_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="YYYY-MM-DD"
            />
          </el-form-item>
          <el-form-item label="推荐人">
            <el-input v-model="form.referrer" />
          </el-form-item>
          <el-form-item label="公司规模">
            <el-input v-model="form.company_size" />
          </el-form-item>
          <el-form-item label="入塾年限">
            <el-input-number
              v-model="form.membership_years"
              :min="0"
              :max="100"
              :precision="1"
              controls-position="right"
            />
          </el-form-item>
          <el-form-item label="推荐人所属分中心">
            <el-input v-model="form.referrer_center" />
          </el-form-item>
          <el-form-item label="利润率">
            <el-input v-model="form.profit_margin" placeholder="例如 12%" />
          </el-form-item>
          <el-form-item class="full" label="备注">
            <el-input v-model="form.notes" type="textarea" :rows="3" />
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
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 18px;
}
.form-grid .full {
  grid-column: 1 / -1;
}
.form-grid :deep(.el-select) {
  width: 100%;
}
.form-grid :deep(.el-date-editor),
.form-grid :deep(.el-input-number) {
  width: 100%;
}
.form-hint {
  margin: -4px 0 20px;
  color: var(--el-text-color-secondary);
}
:global(.member-dialog) {
  max-width: calc(100vw - 40px);
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

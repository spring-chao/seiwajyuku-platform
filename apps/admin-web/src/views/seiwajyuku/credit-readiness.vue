<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import {
  getCreditReadiness,
  applyCreditRules,
  getCreditStorageReadiness,
  prepareCreditStorage,
  type CreditStorageReadiness,
  type CreditReadiness
} from "@/api/credit-readiness";
import { creditSettlementErrorMessage } from "@/utils/creditSettlementError";
import {
  creditStoragePrepareKey,
  submitCreditStoragePrepareOnce
} from "@/utils/creditStoragePrepareJournal";
import {
  creditRuleApplyJournalKey,
  submitCreditRuleApplyOnce
} from "@/utils/creditRuleApplyJournal";

defineOptions({ name: "CreditReadiness" });
const route = useRoute();
const routeToken = computed(() =>
  typeof route.query.sj_canary === "string" ? route.query.sj_canary : undefined
);
const expectedCommit = computed(() =>
  typeof route.query.expected_commit === "string"
    ? route.query.expected_commit
    : undefined
);
const state = ref<CreditReadiness>();
const loading = ref(false);
const executing = ref(false);
const error = ref("");
const actionError = ref("");
const outcome = ref("");
const reason = ref("");
const submitted = ref(false);
const storage = ref<CreditStorageReadiness>();
const storageError = ref("");
const storageExecuting = ref(false);
const storageReason = ref("");
const storageOutcome = ref("");
const storageAttempted = ref(false);
const canPrepare = computed(
  () =>
    storage.value?.can_prepare &&
    (!expectedCommit.value ||
      storage.value.release_commit === expectedCommit.value) &&
    !loading.value &&
    !storageExecuting.value &&
    !storageAttempted.value &&
    storageReason.value.trim().length >= 8
);
try {
  submitted.value = sessionStorage.getItem(creditRuleApplyJournalKey) !== null;
} catch {
  submitted.value = true;
}
const correctBuild = computed(
  () =>
    !expectedCommit.value ||
    state.value?.release_commit === expectedCommit.value
);
const canExecute = computed(
  () =>
    state.value?.status === "READY" &&
    !!state.value.apply_payload &&
    correctBuild.value &&
    !loading.value &&
    !executing.value &&
    !submitted.value &&
    reason.value.trim().length >= 8 &&
    reason.value.trim().length <= 1000
);
const actionLabel = (action: string) =>
  ({
    KEEP: "保留",
    UPDATE: "修正",
    ADD: "补齐",
    REMOVE_UNUSED_PLACEHOLDER: "移除未使用占位项",
    BLOCK: "阻塞"
  })[action] || action;

async function refresh() {
  loading.value = true;
  error.value = "";
  state.value = undefined;
  try {
    state.value = (await getCreditReadiness(routeToken.value)).data;
  } catch (err) {
    error.value = creditSettlementErrorMessage(err, "学分上线准备核验失败");
  } finally {
    loading.value = false;
  }
  storageError.value = "";
  try {
    storage.value = (await getCreditStorageReadiness()).data;
    storageAttempted.value =
      !!storage.value.next_migration &&
      sessionStorage.getItem(
        creditStoragePrepareKey(storage.value.next_migration)
      ) !== null;
  } catch (err) {
    storageError.value = creditSettlementErrorMessage(err, "结算存储核验失败");
    storage.value = undefined;
  }
}

async function executeStorage() {
  if (!canPrepare.value) return;
  const snapshot = storage.value;
  const stage = snapshot.stages.find(
    item => item.version === snapshot.next_migration
  );
  if (!stage) return;
  storageExecuting.value = true;
  storageError.value = "";
  try {
    await submitCreditStoragePrepareOnce(
      sessionStorage,
      stage.version,
      {
        commit: snapshot.release_commit,
        fingerprint: snapshot.baseline_fingerprint,
        submitted_at: new Date().toISOString()
      },
      () => {
        storageAttempted.value = true;
        return prepareCreditStorage(stage.version, {
          expected_release_commit: snapshot.release_commit,
          expected_baseline_fingerprint: snapshot.baseline_fingerprint,
          expected_migration_sha256: stage.sha256,
          execution_reason: storageReason.value.trim()
        });
      }
    );
    storageOutcome.value = `${stage.version} 已完成，正在核验下一步。`;
  } catch (err) {
    storageError.value = creditSettlementErrorMessage(
      err,
      "执行结果需要核验，不会自动重试。请刷新核验。"
    );
  } finally {
    storageExecuting.value = false;
    await refresh();
  }
}

async function executeRules() {
  if (!canExecute.value) return;
  executing.value = true;
  actionError.value = "";
  const payload = {
    ...state.value.apply_payload,
    execution_reason: reason.value.trim()
  };
  try {
    const result = (
      await submitCreditRuleApplyOnce(
        sessionStorage,
        {
          commit: payload.expected_release_commit,
          fingerprint: payload.expected_production_fingerprint,
          submitted_at: new Date().toISOString()
        },
        () => {
          submitted.value = true;
          return applyCreditRules(payload, routeToken.value);
        }
      )
    ).data;
    outcome.value =
      result.status === "APPLIED"
        ? "课程规则已收口，正在重新核验数据库结果。"
        : "课程规则已完成收口，无需重复执行。";
  } catch (err) {
    actionError.value = creditSettlementErrorMessage(
      err,
      "执行结果需要核验，请刷新核验结果；不要重复提交。"
    );
  } finally {
    executing.value = false;
    await refresh();
  }
}
onMounted(refresh);
</script>

<template>
  <div class="credit-readiness">
    <el-card shadow="never">
      <template #header
        ><div class="header">
          <strong>学分上线准备</strong
          ><el-button :loading="loading" :disabled="executing" @click="refresh"
            >刷新核验</el-button
          >
        </div></template
      >
      <p>
        核对课程规则、班级绑定和正式账本。执行规则收口后，继续完成数据库升级与结算验收。
      </p>
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="actionError"
        :title="actionError"
        type="error"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="outcome"
        :title="outcome"
        type="success"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="submitted && state?.status !== 'ALREADY_APPLIED'"
        title="本浏览器已提交过收口请求，结果需核验。请保留当前状态，避免重复执行。"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="state && !correctBuild"
        title="实际运行版本与目标版本不一致，执行已关闭。"
        type="error"
        :closable="false"
        show-icon
      />
      <template v-if="state">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="课程规则"
            >{{ state.rule_count }} /
            {{ state.target_rule_count }} 条</el-descriptions-item
          >
          <el-descriptions-item label="规则版本"
            >{{ state.version.version_label }} ·
            {{ state.version.status }}</el-descriptions-item
          >
          <el-descriptions-item label="正式账本"
            >{{ state.ledger.entry_count }} 条 ·
            {{ state.ledger.points }} 分</el-descriptions-item
          >
          <el-descriptions-item label="待冻结班级">{{
            state.bindings?.total ?? "需核验"
          }}</el-descriptions-item>
          <el-descriptions-item label="核验时间">{{
            state.checked_at
          }}</el-descriptions-item>
          <el-descriptions-item label="运行提交"
            ><code>{{ state.release_commit }}</code></el-descriptions-item
          >
        </el-descriptions>
        <el-alert
          v-if="state.status === 'ALREADY_APPLIED'"
          title="课程规则已与确认口径一致。下一步核验规则绑定和结算表升级。"
          type="success"
          :closable="false"
          show-icon
        />
        <template v-if="state.status !== 'ALREADY_APPLIED'"
          ><el-alert
            v-for="blocker in state.blockers"
            :key="blocker.code + blocker.message"
            :title="blocker.message"
            type="warning"
            :closable="false"
            show-icon
        /></template>
        <p>
          已登记升级：{{
            state.applied_migrations.length
              ? state.applied_migrations.join("、")
              : "0064–0067 尚未登记"
          }}
        </p>
        <el-table :data="state.plan" stripe>
          <el-table-column label="处理" width="180"
            ><template #default="{ row }">{{
              actionLabel(row.action)
            }}</template></el-table-column
          >
          <el-table-column label="当前课程"
            ><template #default="{ row }">{{
              row.before?.course_name ?? "未配置"
            }}</template></el-table-column
          >
          <el-table-column label="目标课程"
            ><template #default="{ row }">{{
              row.after?.course_name ?? "移除"
            }}</template></el-table-column
          >
          <el-table-column label="当前分值" width="110"
            ><template #default="{ row }">{{
              row.before?.credit_points ?? "—"
            }}</template></el-table-column
          >
          <el-table-column label="目标分值" width="110"
            ><template #default="{ row }">{{
              row.after?.credit_points ?? "—"
            }}</template></el-table-column
          >
        </el-table>
        <el-form label-position="top">
          <el-form-item label="执行原因"
            ><el-input
              v-model="reason"
              type="textarea"
              :maxlength="1000"
              placeholder="填写本次课程规则收口的业务原因，至少 8 个字"
              :disabled="executing || submitted"
          /></el-form-item>
          <el-button
            type="primary"
            :disabled="!canExecute"
            :loading="executing"
            @click="executeRules"
            >执行课程规则收口</el-button
          >
        </el-form>
      </template>
    </el-card>
    <el-card shadow="never" style="margin-top: 16px">
      <template #header><strong>结算数据库准备与启用状态</strong></template>
      <p>
        课程规则收口后，依次准备规则绑定、历史来源、期间结构和结算批次。每次只执行一个固定升级，不自动重试，不写入学分。
      </p>
      <el-alert
        v-if="storageError"
        :title="storageError"
        type="error"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="storageOutcome"
        :title="storageOutcome"
        type="success"
        :closable="false"
        show-icon
      />
      <template v-if="storage">
        <el-alert
          v-if="storage.formal_ready"
          title="日常学习正式结算已开启，可按业务记录计算、复核并入账。"
          type="success"
          :closable="false"
          show-icon
        />
        <el-alert
          v-else-if="storage.storage_ready"
          title="结算数据库已就绪，等待发布启用结算配置。"
          type="success"
          :closable="false"
          show-icon
        />
        <el-alert
          v-for="blocker in storage.blockers"
          :key="blocker"
          :title="blocker"
          type="warning"
          :closable="false"
          show-icon
        />
        <el-table :data="storage.stages">
          <el-table-column prop="version" label="升级步骤" width="130" />
          <el-table-column prop="filename" label="固定存储脚本" />
          <el-table-column label="状态" width="130"
            ><template #default="{ row }">{{
              row.applied ? "已完成" : "待完成"
            }}</template></el-table-column
          >
        </el-table>
        <p>
          规则绑定：{{ storage.bindings.generic_frozen }} /
          {{ storage.bindings.total }}；课程绑定：{{
            storage.bindings.course_frozen
          }}
          / {{ storage.bindings.total }}；待补课程引用：{{
            storage.course_references_to_fill
          }}。
        </p>
        <el-form v-if="storage.next_migration" label-position="top">
          <el-form-item label="存储准备原因"
            ><el-input
              v-model="storageReason"
              type="textarea"
              :maxlength="1000"
              placeholder="填写本次准备原因，至少 8 个字"
              :disabled="storageExecuting"
          /></el-form-item>
          <el-button
            type="primary"
            :disabled="!canPrepare"
            :loading="storageExecuting"
            @click="executeStorage"
            >准备 {{ storage.next_migration }} 结算存储</el-button
          >
          <p v-if="storageAttempted">
            本步骤已提交，请刷新核验结果；不重复发送。
          </p>
        </el-form>
      </template>
    </el-card>
  </div>
</template>

<style scoped>
.credit-readiness {
  padding: 16px;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.el-alert,
.el-descriptions,
.el-form {
  margin: 16px 0;
}
code {
  overflow-wrap: anywhere;
}
</style>

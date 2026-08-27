<script setup lang="ts">
import { onMounted, onBeforeUnmount, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { studyMeetingErrorMessage } from "@/utils/studyMeetingError";
import {
  getStudyMeetingRecord,
  getStudyMeetingRecords,
  getStudyMeetingPhoto,
  correctStudyMeetingCourses,
  type StudyMeetingRecord,
  type StudyMeetingRecordDetail
} from "@/api/study-meetings";

defineOptions({ name: "StudyMeetings" });

const loading = ref(false);
const detailLoading = ref(false);
const error = ref("");
const localApiLabel = import.meta.env.DEV
  ? `本地开发验收 · API：${import.meta.env.VITE_API_BASE_URL || "当前站点（未单独配置）"}` : "";
const records = ref<StudyMeetingRecord[]>([]);
const detailVisible = ref(false);
const detail = ref<StudyMeetingRecordDetail>();
const photoUrl = ref("");
const photoLoading = ref(false);
const editingCourses = ref(false);
const savingCourses = ref(false);
const selectedCourses = ref<string[]>([]);
const correctionNote = ref("");
const releasePhoto = () => {
  if (photoUrl.value) URL.revokeObjectURL(photoUrl.value);
  photoUrl.value = "";
};
watch(detailVisible, visible => { if (!visible) releasePhoto(); });
onBeforeUnmount(releasePhoto);
const filters = reactive<{
  status: "" | StudyMeetingRecord["status"];
  meeting_date_from: string;
  meeting_date_to: string;
}>({
  status: "",
  meeting_date_from: "",
  meeting_date_to: ""
});

const statusLabel = (status: StudyMeetingRecord["status"]) =>
  ({ DRAFT: "草稿", SUBMITTED: "已提交", CANCELLED: "已取消" })[status];

const statusType = (status: StudyMeetingRecord["status"]) =>
  ({ DRAFT: "info", SUBMITTED: "success", CANCELLED: "warning" })[status] as
    | "info"
    | "success"
    | "warning";

const formatDateTime = (value?: string | null) =>
  value ? value.replace("T", " ").replace("Z", "") : "—";

// Element Plus exposes table slot rows as a generic DefaultRow in templates;
// the API payload is still typed at the boundary above.
const courseLabel = (record: any) => {
  if (Array.isArray(record.courses)) {
    return record.courses.map(course =>
      `${course.course_name_snapshot}（${course.course_rule_status === "PENDING" ? "学分待配置" : course.course_credit_snapshot + "学分"}）`
    ).join("；") || "未观看课程";
  }
  if (!record.has_course) return "未观看课程";
  if (record.course_rule_status === "PENDING") {
    return `${record.course_name_snapshot || "已选课程"}（学分待配置）`;
  }
  return `${record.course_name_snapshot || "已选课程"}${record.course_credit_snapshot == null ? "" : ` · ${record.course_credit_snapshot} 分`}`;
};

const loadRecords = async () => {
  loading.value = true;
  error.value = "";
  try {
    const response = await getStudyMeetingRecords({
      status: filters.status || undefined,
      meeting_date_from: filters.meeting_date_from || undefined,
      meeting_date_to: filters.meeting_date_to || undefined
    });
    records.value = response.data.records || [];
  } catch (requestError) {
    records.value = [];
    error.value = studyMeetingErrorMessage(requestError);
    ElMessage.error(error.value);
  } finally {
    loading.value = false;
  }
};

const openDetail = async (record: any) => {
  releasePhoto();
  editingCourses.value = false;
  detailVisible.value = true;
  detailLoading.value = true;
  detail.value = undefined;
  try {
    const response = await getStudyMeetingRecord(record.id);
    detail.value = response.data;
  } catch (requestError) {
    ElMessage.error(studyMeetingErrorMessage(requestError, "学习会详情加载"));
  } finally {
    detailLoading.value = false;
  }
};

const viewPhoto = async () => {
  if (!detail.value) return;
  const sessionId = detail.value.id;
  photoLoading.value = true;
  try {
    const blob = await getStudyMeetingPhoto(sessionId);
    if (detail.value?.id === sessionId && detailVisible.value) {
      releasePhoto();
      photoUrl.value = URL.createObjectURL(blob);
    }
  } catch (requestError) {
    ElMessage.error(studyMeetingErrorMessage(requestError, "合影查看"));
  } finally { photoLoading.value = false; }
};

const editCourses = () => {
  selectedCourses.value = detail.value?.courses.map(item => item.course_key) || [];
  correctionNote.value = "";
  editingCourses.value = true;
};

const saveCourses = async () => {
  if (!detail.value || savingCourses.value) return;
  savingCourses.value = true;
  try {
    const response = await correctStudyMeetingCourses(detail.value.id, {
      course_keys: selectedCourses.value,
      expected_course_keys: detail.value.courses.map(item => item.course_key),
      note: correctionNote.value || undefined
    });
    detail.value = response.data;
    editingCourses.value = false;
    ElMessage.success("课程已修正，操作已记录；未产生积分");
    await loadRecords();
  } catch (requestError) {
    ElMessage.error(studyMeetingErrorMessage(requestError, "课程修正"));
  } finally { savingCourses.value = false; }
};

onMounted(loadRecords);
</script>

<template>
  <div class="study-meetings page-container">
    <el-card shadow="never" class="records-card">
      <template #header>
        <div class="records-header">
          <div>
            <div class="records-title">小组学习会记录</div>
            <div class="records-subtitle">
              查看学习记录与合影，按权限修正课程；不做审核、积分结算或组织关系修改。
            </div>
            <div v-if="localApiLabel" class="muted">{{ localApiLabel }}</div>
          </div>
          <el-button :loading="loading" @click="loadRecords">刷新</el-button>
        </div>
      </template>

      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
        class="records-alert"
      />
      <div v-if="error" class="muted records-alert">接口：GET /api/v1/study-meetings/records</div>

      <div class="records-filters">
        <el-select v-model="filters.status" clearable placeholder="全部状态" class="status-select">
          <el-option label="草稿" value="DRAFT" />
          <el-option label="已提交" value="SUBMITTED" />
          <el-option label="已取消" value="CANCELLED" />
        </el-select>
        <el-date-picker
          v-model="filters.meeting_date_from"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="开始日期"
          clearable
        />
        <span class="date-separator">至</span>
        <el-date-picker
          v-model="filters.meeting_date_to"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="结束日期"
          clearable
        />
        <el-button type="primary" :loading="loading" @click="loadRecords">查询</el-button>
      </div>

      <el-table v-loading="loading" :data="records" stripe border class="records-table" row-key="id">
        <el-table-column prop="meeting_date" label="日期" width="125" />
        <el-table-column label="班级 / 小组" min-width="210">
          <template #default="{ row }">
            <div>{{ row.class_name }}</div>
            <div class="muted">{{ row.group_name }} · 第 {{ row.cycle_index }} 周期</div>
          </template>
        </el-table-column>
        <el-table-column label="提交人" width="130">
          <template #default="{ row }">{{ row.creator_name }}</template>
        </el-table-column>
        <el-table-column label="参加人数" width="150">
          <template #default="{ row }">
            {{ row.total_count }} 人
            <div class="muted">本组 {{ row.home_count }} · 跨组 {{ row.cross_group_count }}</div>
          </template>
        </el-table-column>
        <el-table-column label="课程" min-width="220">
          <template #default="{ row }">{{ courseLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无学习会记录" />
        </template>
      </el-table>
    </el-card>

    <el-drawer v-model="detailVisible" title="小组学习会详情" size="620px">
      <div v-loading="detailLoading" class="detail-content">
        <template v-if="detail">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="日期">{{ detail.meeting_date }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusType(detail.status)">{{ statusLabel(detail.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="班级" :span="2">{{ detail.class_name }}</el-descriptions-item>
            <el-descriptions-item label="小组">{{ detail.group_name }}</el-descriptions-item>
            <el-descriptions-item label="学习周期">第 {{ detail.cycle_index }} 周期</el-descriptions-item>
            <el-descriptions-item label="提交人">{{ detail.creator_name }}</el-descriptions-item>
            <el-descriptions-item label="提交时间">{{ formatDateTime(detail.submitted_at || detail.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="课程" :span="2">{{ courseLabel(detail) }}</el-descriptions-item>
          </el-descriptions>

          <div class="attendee-section">
            <div class="detail-title">本组参加（{{ detail.home_attendees.length }}人）</div>
            <div class="attendee-list">
              <el-tag v-for="item in detail.home_attendees" :key="item.id" effect="plain">
                {{ item.name }}
              </el-tag>
              <span v-if="!detail.home_attendees.length" class="muted">无</span>
            </div>
          </div>
          <div class="attendee-section">
            <div class="detail-title">跨组参加（{{ detail.cross_group_attendees.length }}人）</div>
            <div class="attendee-list">
              <el-tag v-for="item in detail.cross_group_attendees" :key="item.id" type="warning" effect="plain">
                {{ item.name }}（{{ item.home_group_name }}）
              </el-tag>
              <span v-if="!detail.cross_group_attendees.length" class="muted">无</span>
            </div>
          </div>
          <section class="attendee-section">
            <div class="detail-title">学习合影</div>
            <template v-if="detail.evidence">
              <el-button :loading="photoLoading" @click="viewPhoto">查看合影</el-button>
              <div class="muted">默认保留7天，到期后清理照片，保留学习记录。</div>
              <img v-if="photoUrl" :src="photoUrl" alt="本次学习合影" class="study-photo" />
            </template>
            <span v-else class="muted">未上传或已到期清理</span>
          </section>
          <section v-if="detail.can_edit_courses" class="attendee-section">
            <el-button v-if="!editingCourses" @click="editCourses">修正课程</el-button>
            <template v-else>
              <div class="detail-title">本次观看的课程（可多选，留空表示无课程）</div>
              <el-select v-model="selectedCourses" multiple filterable placeholder="选择课程" style="width: 100%">
                <el-option v-for="course in detail.course_options" :key="course.course_key"
                  :value="course.course_key"
                  :label="course.course_name + (course.status === 'CONFIGURED' ? ' · ' + course.credit_points + '学分' : ' · 学分待配置')" />
              </el-select>
              <el-input v-model="correctionNote" placeholder="修正备注（选填）" maxlength="1000" class="correction-note" />
              <el-button type="primary" :loading="savingCourses" @click="saveCourses">保存修正</el-button>
              <el-button :disabled="savingCourses" @click="editingCourses = false">取消</el-button>
              <div class="muted">仅修正课程名单，保留已有课程的原积分快照；不修改积分标准。</div>
            </template>
          </section>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.study-meetings { padding: 16px; }
.records-card { min-height: calc(100vh - 110px); }
.records-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.records-title { color: #3b1f24; font-size: 20px; font-weight: 700; }
.records-subtitle { color: #8e7478; font-size: 13px; margin-top: 6px; }
.records-alert { margin-bottom: 16px; }
.records-filters { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }
.status-select { width: 140px; }
.date-separator { color: #9f898d; }
.records-table { width: 100%; }
.muted { color: #9f898d; font-size: 12px; margin-top: 3px; }
.detail-content { min-height: 180px; }
.attendee-section { margin: 22px 0; }
.detail-title { color: #542a31; font-size: 15px; font-weight: 700; margin-bottom: 10px; }
.attendee-list { display: flex; flex-wrap: wrap; gap: 8px; }
.study-photo { width: 100%; max-height: 420px; object-fit: contain; margin-top: 16px; }
.correction-note { margin: 14px 0; }
</style>

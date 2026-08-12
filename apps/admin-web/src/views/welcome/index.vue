<script setup lang="ts">
defineOptions({
  name: "Welcome"
});
</script>

<template>
  <div class="welcome">
    <p>盛和塾综合运营与发展建设平台</p>
    <h1>以目标牵引组织，以服务促进成长</h1>
    <span>请从左侧导航进入运营驾驶舱、学员运营、活动管理或系统设置。</span>
  </div>
</template>

<style scoped>
.welcome {
  min-height: calc(100vh - 140px);
  padding: 56px;
  color: #173f33;
  background:
    radial-gradient(circle at 80% 15%, rgb(50 128 100 / 16%), transparent 28%),
    #f4f8f6;
  border-radius: 16px;
}
.welcome p {
  color: #328064;
  letter-spacing: 0.12em;
}
.welcome h1 {
  max-width: 720px;
  margin: 12px 0;
  font-size: clamp(34px, 5vw, 64px);
  line-height: 1.18;
}
.welcome span {
  color: #657a71;
}
</style>

<script setup lang="ts">
/**
 * 协作讨论卡片：评论列表、撤回、加载更早和提交输入。
 *
 * 评论数据与接口调用仍由 useTaskDetail 管理；这里只承接展示和表单事件。
 */
import { computed } from 'vue'

import { type TaskComment } from '../../../api/tasks'

const props = defineProps<{
  comments: TaskComment[]
  commentLoading: boolean
  commentUnreadNotice: number
  commentsHasMore: boolean
  commentDeletingId: string | null
  commentError: string
  canComment: boolean
  commentBody: string
  commentKind: 'discussion' | 'clue'
  commentSaving: boolean
  formatTime: (value: string | null) => string
  canDeleteComment: (comment: TaskComment) => boolean
}>()

const emit = defineEmits<{
  (event: 'loadMore'): void
  (event: 'removeComment', comment: TaskComment): void
  (event: 'submitComment'): void
  (event: 'update:commentBody', value: string): void
  (event: 'update:commentKind', value: 'discussion' | 'clue'): void
}>()

const text = computed({
  get: () => props.commentBody,
  set: (value: string) => emit('update:commentBody', value),
})

const isClue = computed({
  get: () => props.commentKind === 'clue',
  set: (value: boolean) => emit('update:commentKind', value ? 'clue' : 'discussion'),
})
</script>

<template>
  <section class="card comment-card">
    <div class="card-head">
      <div>
        <h2>协作讨论</h2>
        <p class="desc">
          围绕这条任务留结论、补充现场信息或交接说明。勾选“作为排查线索”的内容会进入下一次 Agent 排查；
          支持 @用户名 提醒，评论可撤回，正文不做编辑。
        </p>
      </div>
      <span class="badge badge-muted">{{ comments.length }} 条</span>
    </div>

    <p v-if="commentLoading" class="desc">正在读取讨论…</p>
    <p v-else-if="!comments.length" class="desc">还没有讨论，可以先补充一条现场信息。</p>
    <p v-if="commentUnreadNotice" class="comment-note">
      本次进入前有 {{ commentUnreadNotice }} 条未读，已自动标记为已读。
    </p>
    <p v-if="commentsHasMore" class="comment-note">
      <button type="button" class="ghost comment-more" :disabled="commentLoading" @click="emit('loadMore')">
        加载更早的讨论
      </button>
    </p>
    <ol v-if="comments.length" class="comment-list">
      <li v-for="item in comments" :key="item.id" class="comment-item">
        <div class="comment-head">
          <strong>{{ item.author_name || `用户 ${item.author_id}` }}</strong>
          <span v-if="item.mentions?.length" class="comment-mention">
            提到 {{ item.mentions.length }} 人
          </span>
          <span v-if="item.kind === 'clue'" class="comment-clue">排查线索</span>
          <span class="comment-time">{{ formatTime(item.created_at) }}</span>
          <button
            v-if="canDeleteComment(item)"
            type="button"
            class="ghost comment-delete"
            :disabled="commentDeletingId === item.id"
            @click="emit('removeComment', item)"
          >
            {{ commentDeletingId === item.id ? '撤回中…' : '撤回' }}
          </button>
        </div>
        <p class="comment-body">{{ item.body }}</p>
      </li>
    </ol>

    <p v-if="commentError" class="action-error">{{ commentError }}</p>
    <div v-if="canComment" class="comment-compose">
      <label class="comment-label" for="task-comment-body">写一条讨论</label>
      <textarea
        id="task-comment-body"
        v-model="text"
        maxlength="2000"
        rows="3"
        placeholder="例如：已核对 host-150 的磁盘增长，建议先清理 /var/log。"
        @keydown.ctrl.enter.prevent="emit('submitComment')"
      />
      <label class="comment-clue-toggle">
        <input v-model="isClue" type="checkbox" />
        作为排查线索：下一次 Agent 排查会读取这条内容，普通讨论不会。
      </label>
      <div class="comment-actions">
        <span class="comment-count">{{ text.trim().length }}/2000</span>
        <button
          type="button"
          class="primary"
          :disabled="commentSaving || !text.trim()"
          @click="emit('submitComment')"
        >
          {{ commentSaving ? '提交中…' : (isClue ? '提交线索' : '提交讨论') }}
        </button>
      </div>
    </div>
    <p v-else class="desc">当前账号只能查看讨论，不能发言。</p>
  </section>
</template>

<style scoped src="../task-detail.css"></style>

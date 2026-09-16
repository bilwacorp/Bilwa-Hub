import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import axios from 'axios'
import { RotateCw, Send, Trash2 } from 'lucide-react'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Select } from '../../components/ui/Select'
import { Input } from '../../components/ui/Input'
import { Button } from '../../components/ui/Button'
import type { NotificationLog, NotificationLogListResponse, NotificationStatus } from '../../types'

const STATUS_VARIANT: Record<NotificationStatus, 'amber' | 'blue' | 'green' | 'red' | 'gray'> = {
  pending: 'amber',
  sending: 'blue',
  sent: 'green',
  failed: 'red',
  cancelled: 'gray',
}

type TestEmailForm = { recipient: string }
type TestWhatsAppForm = { recipient: string; message: string }

function errorDetail(e: unknown): string | undefined {
  return axios.isAxiosError(e) ? (e.response?.data as { detail?: string })?.detail : undefined
}

export default function NotificationsPage() {
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState('')
  const [channelFilter, setChannelFilter] = useState('')
  const [recipientFilter, setRecipientFilter] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['notifications', statusFilter, channelFilter, recipientFilter],
    queryFn: () => api.get<NotificationLogListResponse>('/notifications', {
      params: {
        status: statusFilter || undefined,
        channel: channelFilter || undefined,
        recipient: recipientFilter || undefined,
      },
    }).then((r) => r.data),
    refetchInterval: 30_000,
  })

  const [detail, setDetail] = useState<NotificationLog | null>(null)

  const resendMutation = useMutation({
    mutationFn: (id: string) => api.post(`/notifications/resend/${id}`),
    onSuccess: () => { toast.success('Notification re-queued'); qc.invalidateQueries({ queryKey: ['notifications'] }) },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to resend'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/notifications/${id}`),
    onSuccess: () => { toast.success('Notification deleted'); setDetail(null); qc.invalidateQueries({ queryKey: ['notifications'] }) },
    onError: () => toast.error('Failed to delete'),
  })

  const [showTestEmail, setShowTestEmail] = useState(false)
  const testEmailForm = useForm<TestEmailForm>()
  const testEmailMutation = useMutation({
    mutationFn: (v: TestEmailForm) => api.post('/notifications/test-email', v),
    onSuccess: () => {
      toast.success('Test email queued')
      setShowTestEmail(false)
      testEmailForm.reset()
      qc.invalidateQueries({ queryKey: ['notifications'] })
    },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to send test email'),
  })

  const [showTestWhatsApp, setShowTestWhatsApp] = useState(false)
  const testWhatsAppForm = useForm<TestWhatsAppForm>({ defaultValues: { message: 'This is a test message from BilwaCorp Fleet Hub.' } })
  const testWhatsAppMutation = useMutation({
    mutationFn: (v: TestWhatsAppForm) => api.post('/notifications/test-whatsapp', v),
    onSuccess: () => {
      toast.success('Test WhatsApp message queued')
      setShowTestWhatsApp(false)
      testWhatsAppForm.reset({ message: 'This is a test message from BilwaCorp Fleet Hub.' })
      qc.invalidateQueries({ queryKey: ['notifications'] })
    },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to send test WhatsApp message'),
  })

  const columns: Column<NotificationLog>[] = [
    { key: 'created_at', header: 'Created', render: (n) => formatDate(n.created_at) },
    { key: 'channel', header: 'Channel', render: (n) => <span className="capitalize">{n.channel}</span> },
    { key: 'recipient', header: 'Recipient' },
    { key: 'subject', header: 'Subject', render: (n) => n.subject ?? n.template ?? '—' },
    { key: 'status', header: 'Status', render: (n) => <Badge variant={STATUS_VARIANT[n.status]}>{n.status}</Badge> },
    {
      key: 'actions', header: '', className: 'text-right',
      render: (n) => (
        <div className="flex justify-end gap-1">
          {(n.status === 'failed' || n.status === 'cancelled') && (
            <Button size="sm" variant="ghost" icon={<RotateCw size={14} />} loading={resendMutation.isPending} onClick={() => resendMutation.mutate(n.id)}>Resend</Button>
          )}
          <Button size="sm" variant="secondary" onClick={() => setDetail(n)}>Details</Button>
        </div>
      ),
    },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text">Notifications</h1>
        <div className="flex gap-2">
          <Button variant="secondary" icon={<Send size={15} />} onClick={() => setShowTestEmail(true)}>Test email</Button>
          <Button variant="secondary" icon={<Send size={15} />} onClick={() => setShowTestWhatsApp(true)}>Test WhatsApp</Button>
        </div>
      </div>

      <div className="flex gap-3 mb-4">
        <Select
          className="w-auto" placeholder="All statuses" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
          options={[
            { value: 'pending', label: 'Pending' },
            { value: 'sending', label: 'Sending' },
            { value: 'sent', label: 'Sent' },
            { value: 'failed', label: 'Failed' },
            { value: 'cancelled', label: 'Cancelled' },
          ]}
        />
        <Select
          className="w-auto" placeholder="All channels" value={channelFilter} onChange={(e) => setChannelFilter(e.target.value)}
          options={[
            { value: 'email', label: 'Email' },
            { value: 'whatsapp', label: 'WhatsApp' },
          ]}
        />
        <Input className="w-56" placeholder="Filter by recipient" value={recipientFilter} onChange={(e) => setRecipientFilter(e.target.value)} />
      </div>

      <DataTable columns={columns} data={data?.items ?? []} loading={isLoading} keyExtractor={(n) => n.id} emptyMessage="No notifications sent yet." />

      <Modal
        open={!!detail} onClose={() => setDetail(null)} title="Notification detail" size="md"
        footer={detail && (
          <>
            <Button variant="danger" icon={<Trash2 size={14} />} loading={deleteMutation.isPending} onClick={() => deleteMutation.mutate(detail.id)}>Delete</Button>
            {(detail.status === 'failed' || detail.status === 'cancelled') && (
              <Button icon={<RotateCw size={14} />} loading={resendMutation.isPending} onClick={() => resendMutation.mutate(detail.id)}>Resend</Button>
            )}
          </>
        )}
      >
        {detail && (
          <div className="space-y-2 text-sm">
            <div><span className="text-muted">Channel:</span> <span className="capitalize">{detail.channel}</span> via {detail.provider}</div>
            <div><span className="text-muted">Recipient:</span> {detail.recipient}</div>
            <div><span className="text-muted">Subject:</span> {detail.subject ?? '—'}</div>
            <div><span className="text-muted">Template:</span> {detail.template ?? '—'}</div>
            <div><span className="text-muted">Status:</span> <Badge variant={STATUS_VARIANT[detail.status]}>{detail.status}</Badge></div>
            <div><span className="text-muted">Retries:</span> {detail.retry_count}</div>
            <div><span className="text-muted">Sent at:</span> {detail.sent_at ? formatDate(detail.sent_at) : '—'}</div>
            {detail.error_message && (
              <div>
                <span className="text-muted">Error:</span>
                <pre className="mt-1 whitespace-pre-wrap text-xs bg-background rounded p-2 border border-border">{detail.error_message}</pre>
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={showTestEmail} onClose={() => setShowTestEmail(false)} title="Send test email" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowTestEmail(false)}>Cancel</Button><Button loading={testEmailMutation.isPending} onClick={testEmailForm.handleSubmit((v) => testEmailMutation.mutate(v))}>Send</Button></>}
      >
        <form className="space-y-4">
          <Input label="Recipient email" type="email" {...testEmailForm.register('recipient', { required: true })} />
          <p className="text-xs text-muted">Verifies the configured SMTP provider end-to-end.</p>
        </form>
      </Modal>

      <Modal
        open={showTestWhatsApp} onClose={() => setShowTestWhatsApp(false)} title="Send test WhatsApp message" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowTestWhatsApp(false)}>Cancel</Button><Button loading={testWhatsAppMutation.isPending} onClick={testWhatsAppForm.handleSubmit((v) => testWhatsAppMutation.mutate(v))}>Send</Button></>}
      >
        <form className="space-y-4">
          <Input label="Recipient phone" placeholder="+91XXXXXXXXXX" {...testWhatsAppForm.register('recipient', { required: true })} />
          <Input label="Message" {...testWhatsAppForm.register('message', { required: true })} />
          <p className="text-xs text-muted">Verifies the configured WhatsApp gateway end-to-end.</p>
        </form>
      </Modal>
    </div>
  )
}

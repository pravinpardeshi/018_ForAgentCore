import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AskResponse, AgentApiService } from './agent-api.service';

interface ChatMsg {
  role: 'user' | 'agent';
  html: string;
  error?: boolean;
  typing?: boolean;
  triage?: AskResponse['triage'];
  incidents?: AskResponse['incidents'];
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c] ?? c);
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent {
  private api = inject(AgentApiService);

  prompt = '';
  topK = 5;
  sessionId: string = crypto.randomUUID?.() ?? String(Date.now());
  agent = 'full';
  agents = ['full', 'triage', 'knowledge', 'rca', 'solution'];
  messages: ChatMsg[] = [];
  busy = false;
  agentTarget = 'http://localhost:8080/invocations (via ng serve proxy)';

  newSession(): void {
    this.sessionId = crypto.randomUUID?.() ?? String(Date.now());
    this.push({ role: 'agent', html: `New session started: ${escapeHtml(this.sessionId)}` });
  }

  clear(): void {
    this.messages = [];
  }

  send(): void {
    const text = this.prompt.trim();
    if (!text || this.busy) return;
    const topK = Math.max(1, Math.min(20, this.topK || 5));
    this.push({ role: 'user', html: escapeHtml(text) });
    this.prompt = '';
    this.busy = true;
    const typing = this.push({ role: 'agent', html: 'Thinking…', typing: true });
    this.api.ask({ prompt: text, top_k: topK, session_id: this.sessionId, agent: this.agent })
      .subscribe({
        next: (data) => {
          this.remove(typing);
          this.busy = false;
          this.push({
            role: 'agent',
            html: this.renderAnswer(data.result ?? '(empty result)'),
            triage: data.triage,
            incidents: data.incidents,
          });
          if (data.session_id) this.sessionId = data.session_id;
        },
        error: (err) => {
          this.remove(typing);
          this.busy = false;
          const detail = err?.error && typeof err.error === 'object'
            ? JSON.stringify(err.error)
            : (err?.message ?? 'request failed');
          this.push({ role: 'agent', html: `Error: ${escapeHtml(detail)}`, error: true });
        },
      });
  }

  onKey(e: KeyboardEvent): void {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this.send();
    }
  }

  private renderAnswer(text: string): string {
    return escapeHtml(text).replace(/(\[INC\d+\])/g, '<span class="cite">$1</span>');
  }

  private push(m: ChatMsg): ChatMsg {
    this.messages = [...this.messages, m];
    setTimeout(() => document.getElementById('chat-end')?.scrollIntoView({ block: 'nearest' }));
    return m;
  }

  private remove(m: ChatMsg): void {
    this.messages = this.messages.filter((x) => x !== m);
  }
}

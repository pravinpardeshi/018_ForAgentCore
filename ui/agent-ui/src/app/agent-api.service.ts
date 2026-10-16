import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export interface AskRequest {
  prompt: string;
  top_k: number;
  session_id: string;
  agent: string;
}

export interface Triage {
  category?: string;
  priority?: string;
  urgency?: string;
  assignment_group?: string;
  summary?: string;
}

export interface Incident {
  incident_number?: string;
  short_description?: string;
  resolved_at?: string;
}

export interface AskResponse {
  result?: string;
  session_id?: string;
  agent?: string;
  triage?: Triage;
  incidents?: Incident[];
  error?: string;
}

/** Calls same-origin /api/ask; ng serve proxies it to the agent (see proxy.conf.json). */
@Injectable({ providedIn: 'root' })
export class AgentApiService {
  private http = inject(HttpClient);

  ask(body: AskRequest): Observable<AskResponse> {
    return this.http.post<AskResponse>('/api/ask', body);
  }
}

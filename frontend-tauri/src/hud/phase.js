// Phase -> accent colour (space-separated rgb, same hues as PHASE_COLOR in three/Orb.jsx),
// plus the words the HUD uses for each phase.
export const PHASE_RGB = {
  idle: "30 108 255",
  listening: "0 229 255",
  thinking: "168 85 247",
  acting: "245 158 11",
  speaking: "34 211 238",
  error: "239 68 68",
};

export const PHASE_LABEL = {
  idle: "Ready",
  listening: "Listening",
  thinking: "Thinking",
  acting: "Working",
  speaking: "Speaking",
  error: "Something went wrong",
};

export const AGENT_LABEL = {
  supervisor: "Supervisor",
  dev_agent: "Developer",
  web_agent: "Web",
  os_agent: "Computer",
  rag_agent: "Memory",
};

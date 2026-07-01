# AI Agent Action Decision Prediction Challenge

## Background
Recent AI agents are evolving beyond simple response generation toward systems that decide what action to perform based on a user's request and the surrounding context. In these systems, before complex generation or long-form reasoning takes place, the step that quickly determines which task should be selected and executed in the current context is critically important. In real service environments in particular, these decisions must be made in real time or near real time, so speed and efficiency are key factors alongside accuracy.

However, relying directly on large language models for every decision-making step has limitations in terms of cost and latency, and it can be even less efficient in environments with limited compute resources. For this reason, practical AI agent systems increasingly use a separate lightweight decision-making model before calling an LLM, so that action branching or intent classification can be handled first. This approach can improve the responsiveness of the overall system and optimize resource usage.

Therefore, this challenge focuses on the Action Decision problem, which is the preliminary decision-making stage of an AI agent. The goal is to design and implement a lightweight, high-speed decision-making AI model that can operate reliably in resource-constrained environments. Through this task, participants will be able to understand the structure and division of roles in real AI agent systems and think about more realistic AI agent designs that go beyond generation-centric AI to include decision-making and execution structures.

## Problem Description
Given state data recorded at a specific point in an AI coding agent session, predict the next action the agent will take as one of 14 classes.

Each sample consists of the current user utterance (`current_prompt`), the conversation and action history up to the previous step (`history`), and session metadata such as subscription tier, remaining token budget, and workspace state (`session_meta`).

The prediction targets include the agent's major exploration, editing, execution, and conversation actions, such as reading, searching, and editing files; running shell commands; running tests; and asking the user questions. The evaluation metric is Macro-F1 over the 14 classes.

## Code Submission Competition
This competition is conducted as a "code submission competition," where participants submit inference code (`script.py`) and a trained model as `submit.zip`, rather than submitting a prediction result file.

The following conditions must be satisfied for evaluation to run successfully.

- Inference code execution time: <= 10 minutes
- Package/library installation time: <= 10 minutes
- Submission file size limit: <= 1 GB
- Offline execution environment (no internet access except for package installation)
- Execution environment: T4 GPU (16 GB VRAM), 3 vCPU, 12 GB RAM

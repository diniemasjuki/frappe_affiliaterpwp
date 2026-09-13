"""affiliate/api/ai_service.py

AI integration for affiliate ID verification, backed by Affiliate
Settings (provider, API key, base URL, model).

Both supported providers - OpenAI (ChatGPT) and GLM (Z.ai) - expose the
same OpenAI-compatible `/chat/completions` endpoint with Bearer auth and
accept base64 `image_url` content parts on vision models, so a single
client covers both; only the defaults differ:

- OpenAI: https://api.openai.com/v1 + gpt-4o-mini
- GLM:    https://api.z.ai/api/paas/v4 + glm-4v-flash

Two features use this module, both gated by their own Affiliate Settings
check so either can run alone:

- ID extraction (OCR): a vision call over the affiliate's uploaded ID
  document returns the details printed on it (full name, national ID,
  birthdate, gender, address) as JSON. The portal shows the result to
  the affiliate right after upload so they can apply it to their profile
  with one click, and the raw JSON is kept on the Affiliate Profile.

- Verification: a text-only call that compares the affiliate's entered
  details against the extracted ones and judges whether they genuinely
  match - tolerant of name-order/prefix differences (bin/binti), OCR
  noise and formatting - returning a verdict plus per-field issues.
  The verdict is stored on the Affiliate Profile for the approving
  admin; a mismatch warns the affiliate but never blocks submission.

This module never trusts the model blindly: every response goes through
parse_model_json (models love wrapping JSON in prose/code fences) and
normalize_extracted (gender words in any language, day-first dates,
symbol-ridden IC numbers), so callers always receive well-shaped data.
"""

import base64
import json
import re

import requests
from dateutil import parser as dateutil_parser

import frappe
from frappe import _

# Providers supported by the ai_provider Select on Affiliate Settings.
# Every entry must expose an OpenAI-compatible chat completions API.
DEFAULT_BASE_URLS = {
	"OpenAI": "https://api.openai.com/v1",
	"GLM": "https://api.z.ai/api/paas/v4",
}
DEFAULT_MODELS = {
	"OpenAI": "gpt-4o-mini",
	"GLM": "glm-4v-flash",
}

VISION_TIMEOUT = 90  # seconds - ID photos over a vision model
TEXT_TIMEOUT = 60  # seconds - text-only verification call

EXTRACTION_PROMPT = """\
You are an identity-document OCR assistant. Look at the provided identity \
document (a photo or scan of a national ID card / MyKad / passport) and \
extract the details printed on it.

Respond with ONLY a JSON object - no prose, no markdown fences - using \
exactly these keys (use "" for anything not present or unreadable):

{"full_name": "...", "national_id": "...", "date_of_birth": "...", "gender": "...", "address": "..."}

Rules:
- full_name: the holder's name exactly as printed.
- national_id: the ID/IC number with every symbol and space removed, \
uppercase letters and digits only.
- date_of_birth: the holder's birth date in YYYY-MM-DD format.
- gender: Male or Female, translated to English if printed in another \
language (e.g. Lelaki = Male, Perempuan/Wanita = Female).
- address: the holder's address as printed, line breaks replaced with ", ".
- Never guess: if a field cannot be read clearly, return "" for it.
"""

VERIFICATION_PROMPT = """\
You are verifying an affiliate signup. DATA is what the person entered in \
the signup form; DOCUMENT is what an OCR pass read off their uploaded \
identity document. Judge field by field whether the entered data matches \
the document.

Respond with ONLY a JSON object - no prose, no markdown fences:

{"match": true/false, "confidence": 0-100, "issues": [{"field": "...", "document_value": "...", "entered_value": "...", "note": "..."}]}

Rules:
- Names match despite order or prefix differences (bin/binti/binte, \
middle names, extra spaces) - do not flag those.
- Dates match despite format differences; addresses match when they are \
clearly the same address with minor formatting or abbreviation changes.
- Flag a field only when it genuinely differs (different person, \
different number, different birth date, clearly different address).
- Empty values on either side are never a mismatch on their own - put \
them in issues with a short note, but they must not force match=false \
unless a non-empty value contradicts another non-empty one.
- confidence reflects how certain the overall verdict is (0-100).
- "issues" is [] when everything matches.
"""


def get_ai_config() -> dict:
	"""Resolved AI configuration from Affiliate Settings.

	Returns a dict with the flags, the effective base URL and model
	(provider defaults filled in when the admin left them blank), and a
	`configured` flag - True only when a provider AND an API key are
	present. Callers must treat configured=False as "AI features
	unavailable", never as an error: the wizard keeps working without
	AI, it just loses the extraction/verification panels.
	"""
	from frappe.utils import cint

	settings = frappe.get_cached_doc("Affiliate Settings")
	provider = settings.ai_provider or ""
	enabled = bool(
		provider
		and (cint(settings.enable_id_extraction) or cint(settings.enable_ai_verification))
	)
	key = settings.get_password("ai_api_key", raise_exception=False) if enabled else ""
	return {
		"provider": provider,
		"api_key": key,
		"base_url": (settings.ai_base_url or "").strip() or DEFAULT_BASE_URLS.get(provider, ""),
		"model": (settings.ai_model or "").strip() or DEFAULT_MODELS.get(provider, ""),
		"enable_id_extraction": enabled and cint(settings.enable_id_extraction),
		"enable_ai_verification": enabled and cint(settings.enable_ai_verification),
		"configured": bool(provider and key),
	}


def chat_completion(messages: list, max_tokens: int = 1000, timeout: int = TEXT_TIMEOUT) -> str:
	"""One OpenAI-compatible chat completion call. Returns the assistant
	message's text content, or raises frappe.ValidationError with a
	message that makes sense in the portal UI.
	"""
	config = get_ai_config()
	if not config["configured"]:
		frappe.throw(
			_("AI is not configured on this site (set the provider and API key in Affiliate Settings).")
		)

	url = config["base_url"].rstrip("/") + "/chat/completions"
	try:
		response = requests.post(
			url,
			headers={
				"Authorization": f"Bearer {config['api_key']}",
				"Content-Type": "application/json",
			},
			json={"model": config["model"], "messages": messages, "temperature": 0, "max_tokens": max_tokens},
			timeout=timeout,
		)
	except requests.RequestException as e:
		frappe.throw(
			_("Could not reach the {0} AI service: {1}").format(config["provider"], e)
		)

	if response.status_code != 200:
		detail = ""
		try:
			detail = (response.json().get("error") or {}).get("message") or ""
		except Exception:
			pass
		frappe.throw(
			_("The {0} AI service returned an error ({1}). {2}").format(
				config["provider"], response.status_code, detail
			)
		)

	try:
		return response.json()["choices"][0]["message"]["content"] or ""
	except (KeyError, IndexError, TypeError, ValueError):
		frappe.throw(_("The {0} AI service returned an unexpected response.").format(config["provider"]))


def parse_model_json(text: str) -> dict:
	"""Parse a JSON object out of a model response.

	Models keep wrapping JSON in markdown fences or explaining it in a
	sentence first, so: strip fences, then fall back to the span between
	the first "{" and the last "}" and let json.loads judge.
	"""
	text = (text or "").strip()
	text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
	try:
		return json.loads(text)
	except ValueError:
		pass

	start, end = text.find("{"), text.rfind("}")
	if start > -1 and end > start:
		try:
			return json.loads(text[start : end + 1])
		except ValueError:
			pass
	frappe.throw(_("The AI returned an unreadable response. Please try again."))


def _vision_message(data_url: str, prompt: str) -> list:
	return [
		{
			"role": "user",
			"content": [
				{"type": "text", "text": prompt},
				{"type": "image_url", "image_url": {"url": data_url}},
			],
		}
	]


def profile_document_data_url(profile_name: str, file_url: str) -> str:
	"""The affiliate's uploaded ID document as a base64 data URL.

	Ownership is enforced here, not by the caller: the File must be
	attached to the given Affiliate Profile, so an affiliate can never
	point the extraction endpoint at somebody else's document (or at any
	other file on the site) by guessing file URLs.

	PDFs are accepted exactly like at upload time - the first page is
	rendered to JPEG with PyMuPDF, since the vision endpoints take
	images, not PDFs.
	"""
	from io import BytesIO

	from PIL import Image

	file_url = (file_url or "").split("?")[0]
	if not file_url:
		frappe.throw(_("No ID document was uploaded."))

	attached = frappe.db.get_value(
		"File",
		{
			"file_url": file_url,
			"attached_to_doctype": "Affiliate Profile",
			"attached_to_name": profile_name,
		},
		"name",
	)
	if not attached:
		frappe.throw(_("ID document not found on your profile."), frappe.PermissionError)

	content = frappe.get_doc("File", attached).get_content() or b""

	if content.startswith(b"%PDF"):
		try:
			import fitz

			with fitz.open(stream=content, filetype="pdf") as pdf:
				pix = pdf[0].get_pixmap(dpi=150)
				content = pix.tobytes("jpeg")
			mime = "image/jpeg"
		except Exception:
			frappe.throw(
				_("The uploaded PDF could not be read. Please upload a photo (JPG/PNG) of your ID instead.")
			)
	else:
		mime = "application/octet-stream"
		try:
			with Image.open(BytesIO(content)) as img:
				mime = f"image/{(img.format or '').lower()}"
		except Exception:
			pass
		if not mime.startswith("image/"):
			frappe.throw(_("The uploaded file is not a readable image or PDF."))

	return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def normalize_extracted(raw: dict) -> dict:
	"""Force the extraction result into the shape the rest of the app
	expects: known keys only, normalized national ID (uppercase, no
	symbols - same rule as the form field), ISO birth date, and gender
	reduced to Male/Female whatever language the document printed it in.
	"""
	from affiliate.affiliate.doctype.affiliate_profile.affiliate_profile import (
		normalize_national_id,
	)

	raw = raw if isinstance(raw, dict) else {}

	gender_raw = str(raw.get("gender") or "").strip().lower()
	if gender_raw in ("male", "m", "lelaki"):
		gender = "Male"
	elif gender_raw in ("female", "f", "perempuan", "wanita"):
		gender = "Female"
	else:
		gender = ""

	dob_raw = str(raw.get("date_of_birth") or "").strip()
	date_of_birth = ""
	if dob_raw:
		try:
			date_of_birth = dateutil_parser.parse(dob_raw, dayfirst=True, fuzzy=True).strftime("%Y-%m-%d")
		except (ValueError, OverflowError):
			date_of_birth = dob_raw  # let the raw text through rather than dropping it

	return {
		"full_name": str(raw.get("full_name") or "").strip(),
		"national_id": normalize_national_id(str(raw.get("national_id") or "")),
		"date_of_birth": date_of_birth,
		"gender": gender,
		"address": str(raw.get("address") or "").strip(),
	}


def extract_id_data(data_url: str) -> dict:
	"""OCR one ID document image with the configured vision model and
	return the normalized details found on it.
	"""
	content = chat_completion(
		_vision_message(data_url, EXTRACTION_PROMPT), max_tokens=800, timeout=VISION_TIMEOUT
	)
	return normalize_extracted(parse_model_json(content))


def verify_id_details(entered: dict, document: dict) -> dict:
	"""Judge the affiliate's entered details against the OCR'd document.

	Returns {"status": "Match"|"Mismatch", "confidence": int,
	"issues": [...], "detail": human-readable summary}. Network/model
	errors are caught here and surfaced as status "Error" - a flaky AI
	must never fail the wizard save that this runs inside.
	"""
	payload = {"entered": entered, "document": document}
	try:
		content = chat_completion(
			[
				{
					"role": "user",
					"content": f"{VERIFICATION_PROMPT}\n\nDATA = {json.dumps(payload['entered'], ensure_ascii=False)}\nDOCUMENT = {json.dumps(payload['document'], ensure_ascii=False)}",
				}
			],
			max_tokens=800,
		)
		result = parse_model_json(content)
	except frappe.ValidationError as e:
		return {"status": "Error", "confidence": None, "issues": [], "detail": str(e)}

	issues = result.get("issues") if isinstance(result.get("issues"), list) else []
	try:
		confidence = int(result.get("confidence") or 0)
	except (TypeError, ValueError):
		confidence = 0
	matched = bool(result.get("match"))

	if matched:
		detail = _("All checked details match the uploaded ID document (confidence {0}%).").format(confidence)
	else:
		notes = [
			f"{issue.get('field', '?')}: {issue.get('note', '')}".strip(": ")
			for issue in issues
			if isinstance(issue, dict)
		]
		detail = _("Mismatch detected (confidence {0}%). {1}").format(
			confidence, " ".join(notes) or _("See the extracted data for comparison.")
		)

	return {
		"status": "Match" if matched else "Mismatch",
		"confidence": confidence,
		"issues": [
			{
				"field": str(issue.get("field") or ""),
				"document_value": str(issue.get("document_value") or ""),
				"entered_value": str(issue.get("entered_value") or ""),
				"note": str(issue.get("note") or ""),
			}
			for issue in issues
			if isinstance(issue, dict)
		],
		"detail": detail,
	}

#!/usr/bin/env python3
# server.py
"""
PromptShopMCP

A MCP server that allows you to edit photos using simple text commands. Transform images like a 
professional designer by just describing what you want in natural language.

Features:
- Image generation from text prompts using Google's Gemini models
- Image modification based on text instructions
- Background removal using remove.bg API
- Image hosting and sharing via FreeImage.host

Required environment variables:
- GEMINI_API_KEY: Your Google Gemini API key
- FREEIMAGE_API_KEY: Your FreeImage.host API key
- REMOVEBG_API_KEY: Your remove.bg API key

Usage:
1. Set the required environment variables
2. Run the server using FastMCP
3. Use the provided tools to generate and process images
"""
# Standard library imports
import base64
import hashlib
import os
import sys
import tempfile
from io import BytesIO

# Third-party imports
try:
    import requests
    from PIL import Image
    from google import genai
    from google.genai import types
    from mcp.server.fastmcp import FastMCP
except ImportError as e:
    print(f"Error importing required modules: {str(e)}", file=sys.stderr)
    print("Please install required packages: pip install requests pillow google-generativeai")
    sys.exit(1)

# Create an MCP server
mcp = FastMCP("PromptShopMCP")

# Directory to store generated images
IMAGES_DIR = os.path.join(os.getcwd(), "generated_images")
os.makedirs(IMAGES_DIR, exist_ok=True)

# Track uploaded and generated files
uploaded_files = {}
generated_files = {}

# Helper function for logging to stderr (won't interfere with JSON RPC)
def log_debug(message):
    """Log debug messages to stderr"""
    print(message, file=sys.stderr, flush=True)


def is_safe_image(image_data):
    """
    Perform basic safety checks on an image
    
    Parameters:
    - image_data: Binary image data
    
    Returns:
    - Boolean indicating if image appears safe
    """
    try:
        # Basic validation that this is a valid image file
        img = Image.open(BytesIO(image_data))
        img.verify()

        # Check file size (prevent excessively large images)
        if len(image_data) > 10 * 1024 * 1024:  # 10MB limit
            return False

        return True
    except Exception as e:
        log_debug(f"Image safety check failed: {str(e)}")
        return False


def download_image(url):
    """
    Download an image from a URL
    
    Parameters:
    - url: URL of the image to download
    
    Returns:
    - Tuple of (success_boolean, image_data_or_error_message)
    """
    try:
        # Set a timeout and user agent for the request
        headers = {
            "User-Agent": "GeminiImageModifier/1.0"
        }
        response = requests.get(url, headers=headers, timeout=10)

        # Check if request was successful
        if response.status_code == 200:
            # Check content type is an image
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                return False, f"Not an image: Content-Type is {content_type}"

            # Get image data
            image_data = response.content

            # Check image safety
            if is_safe_image(image_data):
                return True, image_data
            else:
                return False, "Image failed safety checks"

        return False, f"Failed to download image: HTTP {response.status_code}"

    except Exception as e:
        return False, f"Error downloading image: {str(e)}"


def upload_to_freeimage(image_data, filename="gemini_generated_image.jpg"):
    """
    Upload an image to freeimage.host
    
    Parameters:
    - image_data: Binary image data
    - filename: Name to use for the uploaded file
    
    Returns:
    - Tuple of (success_boolean, response_data_or_error_message)
    """
    try:
        # Get API key from environment or use default
        api_key = os.environ.get("FREEIMAGE_API_KEY")

        # Prepare the API endpoint
        upload_url = "https://freeimage.host/api/1/upload"

        # Encode image data as base64
        image_base64 = base64.b64encode(image_data).decode('utf-8')

        # Prepare payload
        payload = {
            'key': api_key,
            'action': 'upload',
            'source': image_base64,
            'format': 'json'
        }

        # Make the API request
        response = requests.post(upload_url, data=payload, timeout=30)

        # Check if request was successful
        if response.status_code == 200:
            response_data = response.json()
            if response_data.get('status_code') == 200:
                return True, response_data

            return False, f"API error: {response_data.get('status_txt', 'Unknown error')}"

        return False, f"HTTP error: {response.status_code}"

    except Exception as e:
        return False, f"Error uploading to freeimage.host: {str(e)}"


@mcp.resource("generated-image://{image_id}")
def get_generated_image(image_id: str) -> bytes:
    """
    Get a previously generated image by its ID
    """
    if image_id in generated_files:
        with open(generated_files[image_id]["path"], "rb") as f:
            return f.read()

    if image_id in uploaded_files:
        with open(uploaded_files[image_id]["path"], "rb") as f:
            return f.read()

    return f"Error: Image with ID {image_id} not found"


@mcp.resource("image-info://{image_id}")
def get_image_info(image_id: str) -> str:
    """
    Get information about a previously generated or uploaded image
    """
    if image_id in generated_files:
        info = generated_files[image_id]
        result = f"Generated image: {info['name']}\nMIME type: {info['mime_type']}\nPrompt: {info['prompt']}"

        # Add external URL if available
        if 'external_url' in info:
            result += f"\nExternal URL: {info['external_url']}"
        if 'display_url' in info:
            result += f"\nDisplay URL: {info['display_url']}"
        if 'thumb_url' in info:
            result += f"\nThumbnail URL: {info['thumb_url']}"

        return result

    if image_id in uploaded_files:
        info = uploaded_files[image_id]
        return f"Uploaded image: {info['name']}\nMIME type: {info['mime_type']}"

    return f"Error: Image with ID {image_id} not found"


@mcp.resource("list-images://")
def list_images() -> str:
    """
    List all available images (both uploaded and generated)
    """
    result = "Uploaded images:\n"
    for img_id, info in uploaded_files.items():
        result += f"- {img_id}: {info['name']} ({info['mime_type']})\n"

    result += "\nGenerated images:\n"
    for img_id, info in generated_files.items():
        result += f"- {img_id}: {info['name']} ({info['mime_type']})"
        if 'external_url' in info:
            result += f" | External URL: {info['external_url']}"
        result += "\n"

    return result


@mcp.tool()
def generate_image_from_url(
    image_url: str,
    prompt: str,
    mime_type: str = "image/jpeg",
    temperature: float = 1.0,
    top_p: float = 0.95,
    top_k: int = 40
) -> str:
    """
    Download an image from URL and use Gemini to modify it based on prompt, 
    returning only the image URL.
    The image will be uploaded to freeimage.host and can be embedded in markdown 
    using ![image](url) format.
    Use this tool to edit images.
    We will give you an image and a url, use the url for further image editing. 
    You must provide the image url in markdown format in the response.
    
    Parameters:
    - image_url: URL of the image to download and modify
    - prompt: Text instruction for how to modify the image, 
      this prompt should be simple and concise, like "add sth to sth" or "remove sth" 
      or "change sth to sth", etc.
    - mime_type: MIME type of the image (default: image/jpeg)
    - temperature: Creativity parameter (0.0-1.0)
    - top_p: Token selection parameter (0.0-1.0)
    - top_k: Token selection parameter (1-100)
    
    Returns:
    - String containing only the image URL
    """
    # Get API key from environment
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    # Download image from URL
    success, result = download_image(image_url)
    if not success:
        raise ValueError(f"Error downloading image from URL: {result}")

    # Create a temporary file for the downloaded image
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
        temp_file.write(result)
        temp_file_path = temp_file.name

    try:
        # Initialize Gemini client
        client = genai.Client(api_key=api_key)

        # Upload file to Gemini
        uploaded_file = client.files.upload(
            file=temp_file_path,
            config={"mime_type": mime_type}
        )

        # Create a conversation history with the uploaded image
        # This is similar to the reference code structure
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_uri(
                        file_uri=uploaded_file.uri,
                        mime_type=mime_type,
                    ),
                    types.Part.from_text(text=prompt),
                ]
            ),
        ]

        # Configure the generation request
        generate_content_config = types.GenerateContentConfig(
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_output_tokens=8192,
            response_modalities=[
                "image",
                "text",
            ],
            response_mime_type="text/plain",
        )

        # Use streaming to capture the response chunks
        image_data = None
        response_mime_type = None
        text_response = ""

        # Process the streamed response
        model_stream = client.models.generate_content_stream(
            model="gemini-2.0-flash-exp",
            contents=contents,
            config=generate_content_config,
        )

        for chunk in model_stream:
            if not chunk.candidates or not chunk.candidates[0].content or not chunk.candidates[0].content.parts:
                continue

            # Check for inline image data
            part = chunk.candidates[0].content.parts[0]
            if hasattr(part, 'inline_data') and part.inline_data:
                image_data = part.inline_data.data
                response_mime_type = part.inline_data.mime_type
                log_debug(f"Received inline image data: {response_mime_type}")
                break  # Once we have the image, we can stop processing

            # Accumulate text response
            if hasattr(chunk, 'text') and chunk.text:
                text_response += chunk.text

        # If we found inline image data
        if image_data:
            # Save generated image to a file for history tracking
            import time
            output_file_name = f"generated_{int(time.time())}.jpg"
            output_file_path = os.path.join(IMAGES_DIR, output_file_name)

            # Save to file for history
            with open(output_file_path, "wb") as f:
                f.write(image_data)

            # Store generated file info
            file_id = f"gen_{len(generated_files)}"
            generated_files[file_id] = {
                "path": output_file_path,
                "name": output_file_name,
                "mime_type": response_mime_type or "image/jpeg",
                "prompt": prompt,
                "source_url": image_url
            }

            # Upload the generated image to freeimage.host
            upload_success, upload_result = upload_to_freeimage(
                image_data, output_file_name)

            if upload_success:
                # Extract the URL from the response
                image_url = upload_result.get('image', {}).get('url')
                thumb_url = upload_result.get(
                    'image', {}).get('thumb', {}).get('url')
                display_url = upload_result.get('image', {}).get('display_url')

                # Add the URL to the generated file info
                generated_files[file_id]['external_url'] = image_url
                generated_files[file_id]['thumb_url'] = thumb_url
                generated_files[file_id]['display_url'] = display_url

                log_debug(f"Image uploaded to freeimage.host: {image_url}")

                # Just return the URL as a string
                return image_url

            log_debug(f"Failed to upload to freeimage.host: {upload_result}")
            raise ValueError(
                f"Failed to upload image to freeimage.host: {upload_result}")

        # If no inline image found, but we have text response, log for debugging
        if text_response:
            log_debug(f"Received text response: {text_response}")

        # If we reach here, no valid image was obtained
        raise ValueError(
            "No image data returned from Gemini. Please try a different prompt or image.")

    finally:
        # Clean up temporary file
        try:
            os.unlink(temp_file_path)
        except:
            pass


@mcp.tool()
def generate_image_from_text(
    prompt: str,
    temperature: float = 1.0,
    top_p: float = 0.95,
    top_k: int = 40
) -> str:
    """
    Generate an image using Gemini based on a text prompt only (no input image required).
    Here's how prompt should be written like, you need to expand on the details:
    - "Close-up photograph of a pair of mismatched socks with different patterns, 
    on a dark blue velvet wooden background."
    - "Dreamy pastel landscape, soft lines, gentle colors, fluffy clouds, rainbow mountains, 
    minimalist"
    - "Group of aliens visiting a farmer's market, trying to understand human food culture. 
    Lots of fresh fruits and vegetables everywhere"
    - "Renaissance vampire king, flower-studded hat, flared nostrils, pink hue, soft gaze, 
    portrait, candid, quarter-turn"
    The image will be uploaded to freeimage.host and can be embedded in markdown 
    using ![image](url) format.
    Use this tool when you need to create a completely new image from a text description.
    You must provide the image url in markdown format in your response.
    
    Parameters:
    - prompt: Text instruction for what image to generate
    - temperature: Creativity parameter (0.0-1.0)
    - top_p: Token selection parameter (0.0-1.0)
    - top_k: Token selection parameter (1-100)
    
    Returns:
    - String containing only the image URL
    """
    # Get API key from environment
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    try:
        # Initialize Gemini client
        client = genai.Client(api_key=api_key)

        # Create content with just the text prompt
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=prompt),
                ]
            ),
        ]

        # Configure the generation request
        generate_content_config = types.GenerateContentConfig(
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_output_tokens=8192,
            response_modalities=[
                "image",
                "text",
            ],
            response_mime_type="text/plain",
        )

        # Use streaming to capture the response chunks
        image_data = None
        response_mime_type = None
        text_response = ""

        # Process the streamed response
        model_stream = client.models.generate_content_stream(
            model="gemini-2.0-flash-exp",
            contents=contents,
            config=generate_content_config,
        )

        for chunk in model_stream:
            if not chunk.candidates or not chunk.candidates[0].content or not chunk.candidates[0].content.parts:
                continue

            # Check for inline image data
            part = chunk.candidates[0].content.parts[0]
            if hasattr(part, 'inline_data') and part.inline_data:
                image_data = part.inline_data.data
                response_mime_type = part.inline_data.mime_type
                log_debug(f"Received inline image data: {response_mime_type}")
                break  # Once we have the image, we can stop processing

            # Accumulate text response
            if hasattr(chunk, 'text') and chunk.text:
                text_response += chunk.text

        # If we found inline image data
        if image_data:
            # Save generated image to a file for history tracking
            import time
            output_file_name = f"generated_{int(time.time())}.jpg"
            output_file_path = os.path.join(IMAGES_DIR, output_file_name)

            # Save to file for history
            with open(output_file_path, "wb") as f:
                f.write(image_data)

            # Store generated file info
            file_id = f"gen_{len(generated_files)}"
            generated_files[file_id] = {
                "path": output_file_path,
                "name": output_file_name,
                "mime_type": response_mime_type or "image/jpeg",
                "prompt": prompt
            }

            # Upload the generated image to freeimage.host
            upload_success, upload_result = upload_to_freeimage(
                image_data, output_file_name)

            if upload_success:
                # Extract the URL from the response
                image_url = upload_result.get('image', {}).get('url')
                thumb_url = upload_result.get(
                    'image', {}).get('thumb', {}).get('url')
                display_url = upload_result.get('image', {}).get('display_url')

                # Add the URL to the generated file info
                generated_files[file_id]['external_url'] = image_url
                generated_files[file_id]['thumb_url'] = thumb_url
                generated_files[file_id]['display_url'] = display_url

                log_debug(f"Image uploaded to freeimage.host: {image_url}")

                # Just return the URL as a string
                return image_url

            log_debug(f"Failed to upload to freeimage.host: {upload_result}")
            raise ValueError(
                f"Failed to upload image to freeimage.host: {upload_result}")

        # If no inline image found, but we have text response, log for debugging
        if text_response:
            log_debug(f"Received text response: {text_response}")

        # If we reach here, no valid image was obtained
        raise ValueError(
            "No image data returned from Gemini. Please try a different prompt.") from None

    except Exception as e:
        log_debug(f"Error in generate_image_from_text: {str(e)}")
        raise ValueError(f"Failed to generate image: {str(e)}") from e


@mcp.tool()
def remove_background(
    image_url: str,
    size: str = "auto"
) -> str:
    """
    Remove the background from an image using the remove.bg API.
    The processed image will be uploaded to freeimage.host and can be embedded in markdown using ![image](url) format.
    You must provide the image url in markdown format in your response.
    
    Parameters:
    - image_url: URL of the image to process
    - size: Output image size (auto, small, medium, hd, 4k, etc.)
    
    Returns:
    - String containing only the URL of the background-removed image
    """
    # Get API key from environment
    api_key = os.environ.get("REMOVEBG_API_KEY")
    if not api_key:
        raise ValueError("REMOVEBG_API_KEY environment variable not set")

    # Download image from URL
    success, image_data = download_image(image_url)
    if not success:
        raise ValueError(f"Error downloading image from URL: {image_data}")

    try:
        # Call the remove.bg API
        response = requests.post(
            'https://api.remove.bg/v1.0/removebg',
            files={'image_file': image_data},
            data={'size': size},
            headers={'X-Api-Key': api_key},
        )

        # Check if request was successful
        if response.status_code != requests.codes.ok:
            raise ValueError(
                f"Remove.bg API error: {response.status_code} - {response.text}")

        # Get the processed image data
        processed_image_data = response.content

        # Save processed image to a file for history tracking
        import time
        output_file_name = f"no_bg_{int(time.time())}.png"
        output_file_path = os.path.join(IMAGES_DIR, output_file_name)

        # Save to file for history
        with open(output_file_path, "wb") as f:
            f.write(processed_image_data)

        # Store generated file info
        file_id = f"gen_{len(generated_files)}"
        generated_files[file_id] = {
            "path": output_file_path,
            "name": output_file_name,
            "mime_type": "image/png",
            "prompt": f"Background removed from {image_url}",
            "source_url": image_url
        }

        # Upload the processed image to freeimage.host
        upload_success, upload_result = upload_to_freeimage(
            processed_image_data, output_file_name)

        if upload_success:
            # Extract the URL from the response
            result_url = upload_result.get('image', {}).get('url')
            thumb_url = upload_result.get(
                'image', {}).get('thumb', {}).get('url')
            display_url = upload_result.get('image', {}).get('display_url')

            # Add the URL to the generated file info
            generated_files[file_id]['external_url'] = result_url
            generated_files[file_id]['thumb_url'] = thumb_url
            generated_files[file_id]['display_url'] = display_url

            log_debug(
                f"Background-removed image uploaded to freeimage.host: {result_url}")

            # Just return the URL as a string
            return result_url

        log_debug(f"Failed to upload to freeimage.host: {upload_result}")
        raise ValueError(
            f"Failed to upload image to freeimage.host: {upload_result}")

    except Exception as e:
        log_debug(f"Error in remove_background: {str(e)}")
        raise ValueError(f"Failed to remove background: {str(e)}") from e
